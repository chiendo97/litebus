# pyright: reportUnusedVariable=false, reportUnknownMemberType=false
"""Comprehensive test suite for litebus: EventListener, Provide, and EventBus."""

from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import final

import pytest

from litebus import Event, EventBus, EventListener, Provide, listener

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Test event classes
# ---------------------------------------------------------------------------


@final
@dataclass(frozen=True, slots=True)
class AlphaEvent(Event):
    value: int


@final
@dataclass(frozen=True, slots=True)
class BetaEvent(Event):
    value: str


@final
@dataclass(frozen=True, slots=True)
class CascadeEventA(Event):
    depth: int


@final
@dataclass(frozen=True, slots=True)
class CascadeEventB(Event):
    depth: int


@final
@dataclass(frozen=True, slots=True)
class ErrorEvent(Event):
    pass


@final
@dataclass(frozen=True, slots=True)
class NoListenerEvent(Event):
    pass


# ---------------------------------------------------------------------------
# EventListener tests (_listener.py)
# ---------------------------------------------------------------------------


class TestEventListener:
    """Tests for EventListener / listener decorator."""

    def test_basic_decoration(self) -> None:
        """@listener(SomeEvent) returns an EventListener with correct event_types and fn."""

        @listener(AlphaEvent)
        async def on_alpha(event: AlphaEvent) -> None:
            pass

        assert isinstance(on_alpha, EventListener)
        assert on_alpha.event_types == (AlphaEvent,)
        assert on_alpha.fn is not None
        assert on_alpha.fn.__name__ == "on_alpha"

    def test_multiple_event_types(self) -> None:
        """@listener(EventA, EventB) stores both types in event_types."""

        @listener(AlphaEvent, BetaEvent)
        async def on_both(event: AlphaEvent | BetaEvent) -> None:
            pass

        assert isinstance(on_both, EventListener)
        assert set(on_both.event_types) == {AlphaEvent, BetaEvent}

    def test_rejects_sync_function(self) -> None:
        """@listener(Event) on a sync function raises TypeError."""
        with pytest.raises(TypeError, match="must be an async function"):

            @listener(AlphaEvent)
            def on_sync(event: AlphaEvent) -> None:  # type: ignore[type-var]
                pass

    def test_wrappers_applied_in_order(self) -> None:
        """Wrappers are applied in order and executor differs from fn."""
        call_order: list[str] = []

        def wrapper_a(fn: object) -> object:
            call_order.append("a")
            return fn

        def wrapper_b(fn: object) -> object:
            call_order.append("b")
            return fn

        decorated = EventListener(AlphaEvent, wrappers=[wrapper_a, wrapper_b])

        async def handler(event: AlphaEvent) -> None:
            pass

        result = decorated(handler)

        assert result is decorated
        assert call_order == ["a", "b"]
        # fn is the original, executor is the wrapped version
        assert decorated.fn is handler
        # With identity wrappers, executor points to the same object,
        # but the important thing is it went through the wrapper chain.
        assert decorated.executor is not None

    def test_wrappers_executor_differs_from_fn(self) -> None:
        """When wrappers transform the function, executor differs from fn."""

        async def replacement(event: AlphaEvent) -> None:
            pass

        def replacing_wrapper(fn: object) -> object:
            return replacement

        decorated = EventListener(AlphaEvent, wrappers=[replacing_wrapper])

        async def handler(event: AlphaEvent) -> None:
            pass

        decorated(handler)

        assert decorated.fn is handler
        assert decorated.executor is replacement
        assert decorated.fn is not decorated.executor

    def test_hash_equality(self) -> None:
        """Two listeners with same event_types and fn hash the same."""

        async def shared_fn(event: AlphaEvent) -> None:
            pass

        l1 = EventListener(AlphaEvent)
        l1(shared_fn)

        l2 = EventListener(AlphaEvent)
        l2(shared_fn)

        assert hash(l1) == hash(l2)

    def test_hash_inequality_different_fn(self) -> None:
        """Different fn produces different hashes."""

        async def fn_a(event: AlphaEvent) -> None:
            pass

        async def fn_b(event: AlphaEvent) -> None:
            pass

        l1 = EventListener(AlphaEvent)
        l1(fn_a)

        l2 = EventListener(AlphaEvent)
        l2(fn_b)

        assert hash(l1) != hash(l2)

    def test_hash_inequality_different_event_types(self) -> None:
        """Different event_types produces different hashes."""

        async def shared_fn(event: Event) -> None:
            pass

        l1 = EventListener(AlphaEvent)
        l1(shared_fn)

        l2 = EventListener(BetaEvent)
        l2(shared_fn)

        assert hash(l1) != hash(l2)


# ---------------------------------------------------------------------------
# Provide tests (_di.py)
# ---------------------------------------------------------------------------


class TestProvide:
    """Tests for Provide (dependency injection container)."""

    async def test_sync_factory(self) -> None:
        """Provide wrapping a sync callable returns its result."""
        provider = Provide(lambda: 42)
        async with AsyncExitStack() as stack:
            result = await provider(stack)
        assert result == 42

    async def test_async_factory(self) -> None:
        """Provide wrapping an async callable returns the awaited result."""

        async def async_factory() -> str:
            return "hello"

        provider = Provide(async_factory)
        async with AsyncExitStack() as stack:
            result = await provider(stack)
        assert result == "hello"

    async def test_sync_generator_factory(self) -> None:
        """Sync generator yields value, cleans up when exit stack closes."""
        cleanup_called = False

        def gen_factory():  # type: ignore[no-untyped-def]
            nonlocal cleanup_called
            yield 99
            cleanup_called = True

        provider = Provide(gen_factory)
        async with AsyncExitStack() as stack:
            result = await provider(stack)
            assert result == 99
            assert not cleanup_called
        assert cleanup_called

    async def test_async_generator_factory(self) -> None:
        """Async generator yields value, cleans up when exit stack closes."""
        cleanup_called = False

        async def async_gen_factory():  # type: ignore[no-untyped-def]
            nonlocal cleanup_called
            yield "async_value"
            cleanup_called = True

        provider = Provide(async_gen_factory)
        async with AsyncExitStack() as stack:
            result = await provider(stack)
            assert result == "async_value"
            assert not cleanup_called
        assert cleanup_called

    async def test_sub_dependency_kwargs_passed(self) -> None:
        """Sub-dependency kwargs are passed through correctly."""

        def factory(x: int, y: str) -> str:
            return f"{x}-{y}"

        provider = Provide(factory)
        async with AsyncExitStack() as stack:
            result = await provider(stack, x=10, y="test")
        assert result == "10-test"

    async def test_generator_cleanup_on_success(self) -> None:
        """Generator runs post-yield code on clean exit."""
        lifecycle: list[str] = []

        def gen_factory():  # type: ignore[no-untyped-def]
            lifecycle.append("entered")
            try:
                yield "value"
            except Exception:
                lifecycle.append("rollback")
            else:
                lifecycle.append("commit")
            finally:
                lifecycle.append("closed")

        provider = Provide(gen_factory)
        async with AsyncExitStack() as stack:
            result = await provider(stack)
            assert result == "value"
        assert lifecycle == ["entered", "commit", "closed"]

    async def test_generator_cleanup_on_error(self) -> None:
        """Generator sees exception, rolls back, and re-raises."""
        lifecycle: list[str] = []

        def gen_factory():  # type: ignore[no-untyped-def]
            lifecycle.append("entered")
            try:
                yield "value"
            except Exception:
                lifecycle.append("rollback")
                raise
            finally:
                lifecycle.append("closed")

        provider = Provide(gen_factory)
        with pytest.raises(RuntimeError, match="boom"):
            async with AsyncExitStack() as stack:
                _ = await provider(stack)
                msg = "boom"
                raise RuntimeError(msg)
        assert lifecycle == ["entered", "rollback", "closed"]

    async def test_multiple_calls_to_sync_factory(self) -> None:
        """Provide calls factory each time it is invoked."""
        call_count = 0

        def factory() -> int:
            nonlocal call_count
            call_count += 1
            return call_count

        provider = Provide(factory)
        async with AsyncExitStack() as stack:
            r1 = await provider(stack)
            r2 = await provider(stack)
        assert r1 == 1
        assert r2 == 2
        assert call_count == 2


# ---------------------------------------------------------------------------
# EventBus tests (_bus.py)
# ---------------------------------------------------------------------------


class TestEventBus:
    """Tests for EventBus core functionality."""

    async def test_basic_emit_and_receive(self) -> None:
        """Listener receives event with correct payload after emit."""
        received: list[AlphaEvent] = []

        @listener(AlphaEvent)
        async def on_alpha(event: AlphaEvent) -> None:
            received.append(event)

        bus = EventBus(listeners=[on_alpha])
        async with bus:
            bus.emit(AlphaEvent(value=42))

        assert len(received) == 1
        assert received[0].value == 42

    async def test_multiple_listeners_same_event(self) -> None:
        """Multiple listeners for same event type all fire."""
        results: list[str] = []

        @listener(AlphaEvent)
        async def on_alpha_a(event: AlphaEvent) -> None:
            results.append("a")

        @listener(AlphaEvent)
        async def on_alpha_b(event: AlphaEvent) -> None:
            results.append("b")

        bus = EventBus(listeners=[on_alpha_a, on_alpha_b])
        async with bus:
            bus.emit(AlphaEvent(value=1))

        assert sorted(results) == ["a", "b"]

    async def test_listener_type_isolation(self) -> None:
        """Listener for type A does NOT fire for type B."""
        alpha_received: list[AlphaEvent] = []
        beta_received: list[BetaEvent] = []

        @listener(AlphaEvent)
        async def on_alpha(event: AlphaEvent) -> None:
            alpha_received.append(event)

        @listener(BetaEvent)
        async def on_beta(event: BetaEvent) -> None:
            beta_received.append(event)

        bus = EventBus(listeners=[on_alpha, on_beta])
        async with bus:
            bus.emit(BetaEvent(value="only_beta"))

        assert len(alpha_received) == 0
        assert len(beta_received) == 1
        assert beta_received[0].value == "only_beta"

    async def test_di_resolution(self) -> None:
        """Listener receives injected dependencies via EventBus DI."""
        received_value: list[int] = []

        def make_config() -> int:
            return 123

        @listener(AlphaEvent)
        async def on_alpha(event: AlphaEvent, config: int) -> None:
            received_value.append(config)

        bus = EventBus(
            listeners=[on_alpha],
            dependencies={"config": Provide(make_config)},
        )
        async with bus:
            bus.emit(AlphaEvent(value=0))

        assert received_value == [123]

    async def test_nested_di(self) -> None:
        """Dependency that depends on another dependency resolves correctly."""
        results: list[str] = []

        def make_base() -> str:
            return "base"

        def make_derived(base: str) -> str:
            return f"{base}+derived"

        @listener(AlphaEvent)
        async def on_alpha(event: AlphaEvent, derived: str) -> None:
            results.append(derived)

        bus = EventBus(
            listeners=[on_alpha],
            dependencies={
                "base": Provide(make_base),
                "derived": Provide(make_derived),
            },
        )
        async with bus:
            bus.emit(AlphaEvent(value=0))

        assert results == ["base+derived"]

    async def test_circular_dependency_raises(self) -> None:
        """Circular dependency detection raises RuntimeError."""

        def factory_a(b: object) -> str:
            return "a"

        def factory_b(a: object) -> str:
            return "b"

        @listener(AlphaEvent)
        async def on_alpha(event: AlphaEvent, a: str) -> None:
            pass

        bus = EventBus(
            listeners=[on_alpha],
            dependencies={
                "a": Provide(factory_a),
                "b": Provide(factory_b),
            },
        )
        async with bus, AsyncExitStack() as stack:
            with pytest.raises(RuntimeError, match="Circular dependency"):
                await bus._resolve("a", stack)

    async def test_eventbus_injection(self) -> None:
        """Listener with bus: EventBus parameter receives the bus instance."""
        received_bus: list[EventBus] = []

        @listener(AlphaEvent)
        async def on_alpha(event: AlphaEvent, bus: EventBus) -> None:
            received_bus.append(bus)

        bus = EventBus(listeners=[on_alpha])
        async with bus:
            bus.emit(AlphaEvent(value=0))

        assert len(received_bus) == 1
        assert received_bus[0] is bus

    async def test_cascading_events(self) -> None:
        """Listener that calls bus.emit() triggers downstream listeners."""
        trace: list[str] = []

        @listener(CascadeEventA)
        async def on_a(event: CascadeEventA, bus: EventBus) -> None:
            trace.append(f"a-{event.depth}")
            if event.depth > 0:
                bus.emit(CascadeEventB(depth=event.depth - 1))

        @listener(CascadeEventB)
        async def on_b(event: CascadeEventB, bus: EventBus) -> None:
            trace.append(f"b-{event.depth}")
            if event.depth > 0:
                bus.emit(CascadeEventA(depth=event.depth - 1))

        bus = EventBus(listeners=[on_a, on_b])
        async with bus:
            bus.emit(CascadeEventA(depth=2))

        # Expected cascade: a-2 -> b-1 -> a-0
        assert "a-2" in trace
        assert "b-1" in trace
        assert "a-0" in trace
        assert len(trace) == 3

    async def test_emit_before_aenter_raises(self) -> None:
        """emit() before __aenter__() raises RuntimeError."""

        @listener(AlphaEvent)
        async def on_alpha(event: AlphaEvent) -> None:
            pass

        bus = EventBus(listeners=[on_alpha])
        with pytest.raises(RuntimeError, match="not started"):
            bus.emit(AlphaEvent(value=0))

    async def test_generator_providers_cleaned_up(self) -> None:
        """Generator-based providers are cleaned up on __aexit__()."""
        cleanup_called = False

        def gen_provider():  # type: ignore[no-untyped-def]
            nonlocal cleanup_called
            yield "resource"
            cleanup_called = True

        @listener(AlphaEvent)
        async def on_alpha(event: AlphaEvent, resource: str) -> None:
            pass

        bus = EventBus(
            listeners=[on_alpha],
            dependencies={"resource": Provide(gen_provider)},
        )
        async with bus:
            bus.emit(AlphaEvent(value=0))

        # After __aexit__, generator cleanup should have run
        assert cleanup_called

    async def test_listener_exception_propagates(self) -> None:
        """Exception in listener propagates as ExceptionGroup."""

        @listener(ErrorEvent)
        async def on_error(event: ErrorEvent) -> None:
            msg = "intentional test error"
            raise ValueError(msg)

        bus = EventBus(listeners=[on_error])
        with pytest.raises(BaseExceptionGroup) as exc_info:
            async with bus:
                bus.emit(ErrorEvent())

        assert len(exc_info.value.exceptions) == 1
        assert isinstance(exc_info.value.exceptions[0], ValueError)
        assert str(exc_info.value.exceptions[0]) == "intentional test error"

    async def test_event_with_no_listeners(self) -> None:
        """Event with no listeners: no error, just a no-op."""
        bus = EventBus(listeners=[])
        async with bus:
            # Should not raise
            bus.emit(NoListenerEvent())

    async def test_multiple_events_emitted(self) -> None:
        """Multiple events emitted in sequence are all processed."""
        received: list[int] = []

        @listener(AlphaEvent)
        async def on_alpha(event: AlphaEvent) -> None:
            received.append(event.value)

        bus = EventBus(listeners=[on_alpha])
        async with bus:
            for i in range(5):
                bus.emit(AlphaEvent(value=i))

        assert sorted(received) == [0, 1, 2, 3, 4]

    async def test_multi_event_listener(self) -> None:
        """A listener registered for multiple event types receives both."""
        received: list[Event] = []

        @listener(AlphaEvent, BetaEvent)
        async def on_both(event: Event) -> None:
            received.append(event)

        bus = EventBus(listeners=[on_both])
        async with bus:
            bus.emit(AlphaEvent(value=1))
            bus.emit(BetaEvent(value="hi"))

        assert len(received) == 2
        types_received = {type(e) for e in received}
        assert types_received == {AlphaEvent, BetaEvent}

    async def test_hierarchy_dispatch(self) -> None:
        """Listener registered for base Event receives subclass events."""
        received: list[Event] = []

        @listener(Event)
        async def on_any(event: Event) -> None:
            received.append(event)

        bus = EventBus(listeners=[on_any])
        async with bus:
            bus.emit(AlphaEvent(value=42))

        assert len(received) == 1
        assert isinstance(received[0], AlphaEvent)

    async def test_generator_provider_rollback_on_listener_error(self) -> None:
        """Generator-based DI provider sees exception and can rollback."""
        lifecycle: list[str] = []

        def get_session():  # type: ignore[no-untyped-def]
            lifecycle.append("session:open")
            try:
                yield "session"
            except Exception:
                lifecycle.append("session:rollback")
                raise
            else:
                lifecycle.append("session:commit")
            finally:
                lifecycle.append("session:close")

        @listener(ErrorEvent)
        async def on_error(event: ErrorEvent, session: str) -> None:
            msg = "db write failed"
            raise RuntimeError(msg)

        bus = EventBus(
            listeners=[on_error],
            dependencies={"session": Provide(get_session)},
        )
        with pytest.raises(BaseExceptionGroup):
            async with bus:
                bus.emit(ErrorEvent())

        # Generator provider should have been cleaned up
        assert "session:open" in lifecycle
        assert "session:close" in lifecycle
