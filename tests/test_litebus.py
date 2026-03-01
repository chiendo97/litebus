"""Comprehensive test suite for litebus: EventListener, Provide, and EventBus."""

# pyright: reportUnusedParameter=false

from collections.abc import AsyncGenerator, Callable, Generator
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

    def test_decorator_stores_event_type_and_fn(self) -> None:
        """@listener(SomeEvent) returns an EventListener with correct event_types and fn."""

        @listener(AlphaEvent)
        async def on_alpha(event: AlphaEvent) -> None:
            pass

        assert isinstance(on_alpha, EventListener)
        assert on_alpha.event_types == (AlphaEvent,)
        assert on_alpha.fn is not None
        assert on_alpha.fn.__name__ == "on_alpha"

    def test_decorator_accepts_multiple_event_types(self) -> None:
        """@listener(EventA, EventB) stores both types in event_types."""

        @listener(AlphaEvent, BetaEvent)
        async def on_both(event: AlphaEvent | BetaEvent) -> None:
            pass

        assert isinstance(on_both, EventListener)
        assert set(on_both.event_types) == {AlphaEvent, BetaEvent}

    def test_decorator_rejects_sync_function(self) -> None:
        """@listener(Event) on a sync function raises TypeError."""
        el: EventListener[AlphaEvent] = EventListener(AlphaEvent)

        def sync_handler(event: AlphaEvent) -> None:
            pass

        with pytest.raises(TypeError, match="must be an async function"):
            _ = el(sync_handler)  # pyright: ignore[reportArgumentType]

    def test_wrappers_applied_in_order(self) -> None:
        """Wrappers are applied in the given order and produce an executor."""
        call_order: list[str] = []

        def wrapper_a(fn: Callable[..., object]) -> Callable[..., object]:
            call_order.append("a")
            return fn

        def wrapper_b(fn: Callable[..., object]) -> Callable[..., object]:
            call_order.append("b")
            return fn

        decorated = EventListener(AlphaEvent, wrappers=[wrapper_a, wrapper_b])

        async def handler(event: AlphaEvent) -> None:
            pass

        result = decorated(handler)

        assert result is decorated
        assert call_order == ["a", "b"]
        assert decorated.fn is handler
        assert decorated.executor is not None

    def test_wrappers_executor_differs_when_wrapper_replaces_fn(self) -> None:
        """When a wrapper replaces the function, executor differs from fn."""

        async def replacement(event: AlphaEvent) -> None:
            pass

        def replacing_wrapper(fn: Callable[..., object]) -> Callable[..., object]:
            return replacement

        decorated = EventListener(AlphaEvent, wrappers=[replacing_wrapper])

        async def handler(event: AlphaEvent) -> None:
            pass

        _ = decorated(handler)

        assert decorated.fn is handler
        assert decorated.executor is replacement
        assert decorated.fn is not decorated.executor

    def test_listeners_with_same_fn_and_types_hash_equal(self) -> None:
        """Two listeners with same event_types and fn hash the same and are equal."""

        async def shared_fn(event: AlphaEvent) -> None:
            pass

        l1 = EventListener(AlphaEvent)
        _ = l1(shared_fn)

        l2 = EventListener(AlphaEvent)
        _ = l2(shared_fn)

        assert hash(l1) == hash(l2)
        assert l1 == l2

    def test_listeners_with_different_fn_hash_unequal(self) -> None:
        """Different fn produces different hashes."""

        async def fn_a(event: AlphaEvent) -> None:
            pass

        async def fn_b(event: AlphaEvent) -> None:
            pass

        l1 = EventListener(AlphaEvent)
        _ = l1(fn_a)

        l2 = EventListener(AlphaEvent)
        _ = l2(fn_b)

        assert hash(l1) != hash(l2)
        assert l1 != l2

    def test_listeners_with_different_event_types_hash_unequal(self) -> None:
        """Different event_types produces different hashes."""

        async def shared_fn(event: Event) -> None:
            pass

        l1 = EventListener(AlphaEvent)
        _ = l1(shared_fn)

        l2 = EventListener(BetaEvent)
        _ = l2(shared_fn)

        assert hash(l1) != hash(l2)
        assert l1 != l2

    def test_equality_returns_not_implemented_for_non_listener(self) -> None:
        """EventListener.__eq__ returns NotImplemented for non-EventListener."""

        @listener(AlphaEvent)
        async def on_alpha(event: AlphaEvent) -> None:
            pass

        assert on_alpha != "not a listener"
        assert on_alpha != 42

    def test_listener_without_fn_has_none_attributes(self) -> None:
        """An EventListener that has not been used as a decorator has None fn/executor."""
        el: EventListener[AlphaEvent] = EventListener(AlphaEvent)
        assert el.fn is None
        assert el.executor is None


# ---------------------------------------------------------------------------
# Provide tests (_di.py)
# ---------------------------------------------------------------------------


class TestProvide:
    """Tests for Provide (dependency injection container)."""

    async def test_sync_factory_returns_value(self) -> None:
        """Provide wrapping a sync callable returns its result."""
        provider = Provide(lambda: 42)
        async with AsyncExitStack() as stack:
            result = await provider(stack)
        assert result == 42

    async def test_async_factory_returns_awaited_value(self) -> None:
        """Provide wrapping an async callable returns the awaited result."""

        async def async_factory() -> str:
            return "hello"

        provider = Provide(async_factory)
        async with AsyncExitStack() as stack:
            result = await provider(stack)
        assert result == "hello"

    async def test_sync_generator_yields_and_cleans_up(self) -> None:
        """Sync generator yields value, cleans up when exit stack closes."""
        cleanup_called = False

        def gen_factory() -> Generator[int, None, None]:
            nonlocal cleanup_called
            yield 99
            cleanup_called = True

        provider = Provide(gen_factory)
        async with AsyncExitStack() as stack:
            result = await provider(stack)
            assert result == 99
            assert not cleanup_called
        assert cleanup_called

    async def test_async_generator_yields_and_cleans_up(self) -> None:
        """Async generator yields value, cleans up when exit stack closes."""
        cleanup_called = False

        async def async_gen_factory() -> AsyncGenerator[str, None]:
            nonlocal cleanup_called
            yield "async_value"
            cleanup_called = True

        provider = Provide(async_gen_factory)
        async with AsyncExitStack() as stack:
            result = await provider(stack)
            assert result == "async_value"
            assert not cleanup_called
        assert cleanup_called

    async def test_kwargs_forwarded_to_factory(self) -> None:
        """Sub-dependency kwargs are passed through correctly."""

        def factory(x: int, y: str) -> str:
            return f"{x}-{y}"

        provider = Provide(factory)
        async with AsyncExitStack() as stack:
            result = await provider(stack, x=10, y="test")
        assert result == "10-test"

    async def test_sync_generator_commits_on_clean_exit(self) -> None:
        """Generator runs else/finally code on clean exit."""
        lifecycle: list[str] = []

        def gen_factory() -> Generator[str, None, None]:
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

    async def test_sync_generator_rolls_back_on_error(self) -> None:
        """Generator sees exception, rolls back, and re-raises."""
        lifecycle: list[str] = []

        def gen_factory() -> Generator[str, None, None]:
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

    async def test_async_generator_commits_on_clean_exit(self) -> None:
        """Async generator runs else/finally code on clean exit."""
        lifecycle: list[str] = []

        async def async_gen_factory() -> AsyncGenerator[str, None]:
            lifecycle.append("entered")
            try:
                yield "value"
            except Exception:
                lifecycle.append("rollback")
            else:
                lifecycle.append("commit")
            finally:
                lifecycle.append("closed")

        provider = Provide(async_gen_factory)
        async with AsyncExitStack() as stack:
            result = await provider(stack)
            assert result == "value"
        assert lifecycle == ["entered", "commit", "closed"]

    async def test_async_generator_rolls_back_on_error(self) -> None:
        """Async generator sees exception and rolls back."""
        lifecycle: list[str] = []

        async def async_gen_factory() -> AsyncGenerator[str, None]:
            lifecycle.append("entered")
            try:
                yield "value"
            except Exception:
                lifecycle.append("rollback")
                raise
            finally:
                lifecycle.append("closed")

        provider = Provide(async_gen_factory)
        with pytest.raises(RuntimeError, match="boom"):
            async with AsyncExitStack() as stack:
                _ = await provider(stack)
                msg = "boom"
                raise RuntimeError(msg)
        assert lifecycle == ["entered", "rollback", "closed"]

    async def test_non_cached_factory_called_each_invocation(self) -> None:
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

    # -- basic emit/receive --

    async def test_listener_receives_emitted_event(self) -> None:
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

    async def test_multiple_listeners_all_receive_same_event(self) -> None:
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

    async def test_listener_only_receives_matching_event_type(self) -> None:
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

    async def test_multi_event_listener_receives_all_registered_types(self) -> None:
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

    async def test_base_event_listener_receives_subclass_events(self) -> None:
        """Listener registered for base Event receives subclass events via MRO."""
        received: list[Event] = []

        @listener(Event)
        async def on_any(event: Event) -> None:
            received.append(event)

        bus = EventBus(listeners=[on_any])
        async with bus:
            bus.emit(AlphaEvent(value=42))

        assert len(received) == 1
        assert isinstance(received[0], AlphaEvent)

    async def test_multiple_sequential_emits_all_processed(self) -> None:
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

    async def test_emit_with_no_matching_listeners_is_noop(self) -> None:
        """Event with no listeners: no error, just a no-op."""
        bus = EventBus(listeners=[])
        async with bus:
            bus.emit(NoListenerEvent())

    # -- dependency injection --

    async def test_listener_receives_injected_dependency(self) -> None:
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

    async def test_nested_dependency_resolution(self) -> None:
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

    async def test_circular_dependency_raises_runtime_error(self) -> None:
        """Circular dependency detection raises RuntimeError during emit."""

        # Parameter names must match dependency keys for resolution to walk the cycle.
        def factory_a(b: object) -> str:  # noqa: ARG005
            return "a"

        def factory_b(a: object) -> str:  # noqa: ARG005
            return "b"

        @listener(AlphaEvent)
        async def on_alpha(event: AlphaEvent, a: str) -> None:  # noqa: ARG005
            pass

        bus = EventBus(
            listeners=[on_alpha],
            dependencies={
                "a": Provide(factory_a),
                "b": Provide(factory_b),
            },
        )
        with pytest.raises(BaseExceptionGroup) as exc_info:
            async with bus:
                bus.emit(AlphaEvent(value=0))

        exceptions = exc_info.value.exceptions
        assert any(
            isinstance(e, RuntimeError) and "Circular dependency" in str(e)
            for e in exceptions
        )

    async def test_eventbus_self_injection(self) -> None:
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

    # -- cascading events --

    async def test_cascading_emit_triggers_downstream_listeners(self) -> None:
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

        assert "a-2" in trace
        assert "b-1" in trace
        assert "a-0" in trace
        assert len(trace) == 3

    # -- error handling --

    async def test_emit_before_context_entry_raises(self) -> None:
        """emit() before __aenter__() raises RuntimeError."""

        @listener(AlphaEvent)
        async def on_alpha(event: AlphaEvent) -> None:
            pass

        bus = EventBus(listeners=[on_alpha])
        with pytest.raises(RuntimeError, match="not started"):
            bus.emit(AlphaEvent(value=0))

    async def test_listener_exception_propagates_as_exception_group(self) -> None:
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

    # -- generator provider lifecycle --

    async def test_generator_provider_cleaned_up_on_exit(self) -> None:
        """Generator-based providers are cleaned up on __aexit__()."""
        cleanup_called = False

        def gen_provider() -> Generator[str, None, None]:
            nonlocal cleanup_called
            yield "resource"
            cleanup_called = True

        @listener(AlphaEvent)
        async def on_alpha(event: AlphaEvent, resource: str) -> None:  # noqa: ARG005
            pass

        bus = EventBus(
            listeners=[on_alpha],
            dependencies={"resource": Provide(gen_provider)},
        )
        async with bus:
            bus.emit(AlphaEvent(value=0))

        assert cleanup_called

    async def test_generator_provider_rollback_on_listener_error(self) -> None:
        """Generator-based DI provider sees exception and can rollback."""
        lifecycle: list[str] = []

        def get_session() -> Generator[str, None, None]:
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
        async def on_error(event: ErrorEvent, session: str) -> None:  # noqa: ARG005
            msg = "db write failed"
            raise RuntimeError(msg)

        bus = EventBus(
            listeners=[on_error],
            dependencies={"session": Provide(get_session)},
        )
        with pytest.raises(BaseExceptionGroup):
            async with bus:
                bus.emit(ErrorEvent())

        assert "session:open" in lifecycle
        assert "session:close" in lifecycle

    # -- max_concurrency --

    async def test_max_concurrency_limits_parallel_listeners(self) -> None:
        """max_concurrency limits how many listeners run in parallel."""
        import anyio

        max_concurrent = 0
        current_concurrent = 0

        @listener(AlphaEvent)
        async def on_alpha(event: AlphaEvent) -> None:
            nonlocal max_concurrent, current_concurrent
            current_concurrent += 1
            if current_concurrent > max_concurrent:
                max_concurrent = current_concurrent
            await anyio.sleep(0.01)
            current_concurrent -= 1

        bus = EventBus(listeners=[on_alpha], max_concurrency=2)
        async with bus:
            for i in range(6):
                bus.emit(AlphaEvent(value=i))

        assert max_concurrent <= 2

    # -- bus reuse --

    async def test_bus_can_be_used_as_context_manager_multiple_times(self) -> None:
        """EventBus can be entered and exited multiple times."""
        received: list[int] = []

        @listener(AlphaEvent)
        async def on_alpha(event: AlphaEvent) -> None:
            received.append(event.value)

        bus = EventBus(listeners=[on_alpha])

        async with bus:
            bus.emit(AlphaEvent(value=1))

        async with bus:
            bus.emit(AlphaEvent(value=2))

        assert sorted(received) == [1, 2]
