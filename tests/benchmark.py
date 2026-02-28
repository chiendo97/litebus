"""Benchmark suite for litebus using pytest-benchmark."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import anyio
import pytest
from pytest_benchmark.fixture import BenchmarkFixture

from litebus import Event, EventBus, Provide, listener

# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SimpleEvent(Event):
    value: int


@dataclass(frozen=True, slots=True)
class DIEvent(Event):
    value: int


@dataclass(frozen=True, slots=True)
class CascadeA(Event):
    depth: int


@dataclass(frozen=True, slots=True)
class CascadeB(Event):
    depth: int


@dataclass(frozen=True, slots=True)
class FanOutEvent(Event):
    value: int


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


class ServiceA:
    pass


class ServiceB:
    def __init__(self, a: ServiceA) -> None:
        self.a = a


def get_service_a() -> ServiceA:
    return ServiceA()


def get_service_b(a: ServiceA) -> ServiceB:
    return ServiceB(a)


# ---------------------------------------------------------------------------
# Listeners
# ---------------------------------------------------------------------------

counts: dict[str, int] = {}


def _inc(key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


@listener(SimpleEvent)
async def on_simple(event: SimpleEvent) -> None:
    _inc("simple")


@listener(DIEvent)
async def on_di(event: DIEvent, a: ServiceA, b: ServiceB) -> None:
    _inc("di")


@listener(CascadeA)
async def on_cascade_a(event: CascadeA, bus: EventBus) -> None:
    _inc("cascade")
    if event.depth > 0:
        bus.emit(CascadeB(depth=event.depth - 1))


@listener(CascadeB)
async def on_cascade_b(event: CascadeB, bus: EventBus) -> None:
    _inc("cascade")
    if event.depth > 0:
        bus.emit(CascadeA(depth=event.depth - 1))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

N = 10_000


@pytest.fixture(autouse=True)
def _reset_counts() -> None:
    counts.clear()


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------


def test_simple(benchmark: BenchmarkFixture) -> None:
    """Emit N events with a single no-DI listener."""

    async def run() -> None:
        bus = EventBus(listeners=[on_simple])
        async with bus:
            for i in range(N):
                bus.emit(SimpleEvent(value=i))

    benchmark.pedantic(anyio.run, args=(run,), rounds=10, warmup_rounds=2)


def test_di(benchmark: BenchmarkFixture) -> None:
    """Emit N events where each listener resolves two dependencies."""

    async def run() -> None:
        bus = EventBus(
            listeners=[on_di],
            dependencies={
                "a": Provide(get_service_a),
                "b": Provide(get_service_b),
            },
        )
        async with bus:
            for i in range(N):
                bus.emit(DIEvent(value=i))

    benchmark.pedantic(anyio.run, args=(run,), rounds=10, warmup_rounds=2)


def test_fanout_10(benchmark: BenchmarkFixture) -> None:
    """Emit N events each handled by 10 listeners."""
    listeners: list[Any] = []
    for _ in range(10):

        @listener(FanOutEvent)
        async def _on_fanout(event: FanOutEvent) -> None:
            _inc("fanout")

        listeners.append(_on_fanout)

    async def run() -> None:
        bus = EventBus(listeners=listeners)
        async with bus:
            for i in range(N):
                bus.emit(FanOutEvent(value=i))

    benchmark.pedantic(anyio.run, args=(run,), rounds=10, warmup_rounds=2)


def test_fanout_50(benchmark: BenchmarkFixture) -> None:
    """Emit N events each handled by 50 listeners."""
    listeners: list[Any] = []
    for _ in range(50):

        @listener(FanOutEvent)
        async def _on_fanout(event: FanOutEvent) -> None:
            _inc("fanout")

        listeners.append(_on_fanout)

    async def run() -> None:
        bus = EventBus(listeners=listeners)
        async with bus:
            for i in range(N):
                bus.emit(FanOutEvent(value=i))

    benchmark.pedantic(anyio.run, args=(run,), rounds=10, warmup_rounds=2)


def test_cascade_100(benchmark: BenchmarkFixture) -> None:
    """One event cascading 100 times (A -> B -> A -> ...)."""

    async def run() -> None:
        bus = EventBus(listeners=[on_cascade_a, on_cascade_b])
        async with bus:
            bus.emit(CascadeA(depth=100))

    benchmark.pedantic(anyio.run, args=(run,), rounds=10, warmup_rounds=2)


def test_cascade_1000(benchmark: BenchmarkFixture) -> None:
    """One event cascading 1000 times."""

    async def run() -> None:
        bus = EventBus(listeners=[on_cascade_a, on_cascade_b])
        async with bus:
            bus.emit(CascadeA(depth=1000))

    benchmark.pedantic(anyio.run, args=(run,), rounds=10, warmup_rounds=2)
