# Copyright 2026 chiendo97

import inspect
import logging
import typing
from collections import defaultdict
from collections.abc import Callable, Sequence
from types import TracebackType
from typing import Self, final

import anyio
import anyio.abc

from ._di import Provide
from ._types import Event as Event
from ._types import Listener

logger = logging.getLogger(__name__)


def _is_event_param(annotation: type, event: Event) -> bool:
    """Check if a parameter annotation matches the event type.

    Returns:
        Whether the annotation matches the event type.
    """
    try:
        return isinstance(event, annotation)
    except TypeError:
        return False


@final
class EventBus:
    """Event emitter with dependency injection for listeners."""

    _listeners: defaultdict[type, set[Listener]]
    _dependencies: dict[str, Provide]
    _tg: anyio.abc.TaskGroup | None
    _limiter: anyio.CapacityLimiter | None
    _on_listener_error: Callable[[Exception, Listener, Event], None] | None

    def __init__(
        self,
        listeners: Sequence[Listener] | None = None,
        dependencies: dict[str, Provide] | None = None,
        *,
        max_concurrency: int | None = None,
        on_listener_error: Callable[[Exception, Listener, Event], None] | None = None,
    ) -> None:
        self._listeners = defaultdict(set)
        self._dependencies = dependencies or {}
        self._tg = None
        self._limiter = (
            anyio.CapacityLimiter(max_concurrency)
            if max_concurrency is not None
            else None
        )
        self._on_listener_error = on_listener_error

        for lst in listeners or []:
            for event_type in lst.event_types:
                self._listeners[event_type].add(lst)
                logger.debug("Registered listener %s for %s", lst, event_type.__name__)

        logger.debug(
            "EventBus initialized with %d listener(s) and %d dependency(ies)",
            sum(len(v) for v in self._listeners.values()),
            len(self._dependencies),
        )

    # -- dependency resolution --

    async def _resolve(
        self,
        name: str,
        _resolving: frozenset[str] | None = None,
    ) -> object:
        """Recursively resolve a single dependency by name.

        Returns:
            The resolved dependency value.

        Raises:
            RuntimeError: If a circular dependency is detected.
        """
        resolving = _resolving or frozenset()
        if name in resolving:
            msg = f"Circular dependency: {' -> '.join(resolving)} -> {name}"
            raise RuntimeError(msg)

        provider = self._dependencies[name]
        resolving |= {name}

        # resolve sub-dependencies of this provider
        sig = inspect.signature(provider.dependency)
        sub_kwargs: dict[str, object] = {}
        for param_name in sig.parameters:
            if param_name in self._dependencies:
                sub_kwargs[param_name] = await self._resolve(param_name, resolving)

        logger.debug(
            "Resolving dependency %r (chain: %s)", name, " -> ".join(resolving)
        )
        result = await provider(**sub_kwargs)
        logger.debug("Resolved dependency %r -> %r", name, type(result).__name__)
        return result

    async def _build_kwargs(
        self,
        fn: Callable[..., object],
        event: Event,
    ) -> dict[str, object]:
        """Build the full kwargs dict for a listener call.

        Returns:
            Mapping of parameter names to resolved values.
        """
        sig = inspect.signature(fn)
        hints = typing.get_type_hints(fn)
        kwargs: dict[str, object] = {}

        for name in sig.parameters:
            annotation = hints.get(name)
            if annotation is EventBus:
                kwargs[name] = self
            elif isinstance(annotation, type) and _is_event_param(annotation, event):
                kwargs[name] = event
            elif name in self._dependencies:
                kwargs[name] = await self._resolve(name)

        logger.debug(
            "Built kwargs for %s: %s",
            getattr(fn, "__name__", fn),
            list(kwargs.keys()),
        )
        return kwargs

    # -- listener execution --

    async def _call_listener(
        self,
        lst: Listener,
        event: Event,
    ) -> None:
        try:
            if lst.fn is None:
                return

            if self._limiter is not None:
                await self._limiter.acquire()

            try:
                logger.debug(
                    "Calling listener %s for %s",
                    getattr(lst.fn, "__name__", lst.fn),
                    type(event).__name__,
                )
                kwargs = await self._build_kwargs(lst.fn, event)
                executor = lst.executor or lst.fn
                await executor(**kwargs)
                logger.debug(
                    "Listener %s completed for %s",
                    getattr(lst.fn, "__name__", lst.fn),
                    type(event).__name__,
                )
            finally:
                if self._limiter is not None:
                    self._limiter.release()
        except Exception as exc:
            logger.exception(
                "Error in listener %s",
                getattr(lst.fn, "__name__", lst.fn),
            )
            if self._on_listener_error is not None:
                self._on_listener_error(exc, lst, event)

    # -- context manager --

    async def __aenter__(self) -> Self:
        logger.debug("EventBus starting")
        self._tg = anyio.create_task_group()
        _ = await self._tg.__aenter__()
        logger.debug("EventBus started")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        logger.debug("EventBus shutting down")
        if self._tg:
            _ = await self._tg.__aexit__(exc_type, exc_val, exc_tb)
        self._tg = None

        for provider in self._dependencies.values():
            await provider.aclose()
        logger.debug("EventBus stopped")

    # -- public API --

    def emit(self, event: Event) -> None:
        """Fire-and-forget: emit a typed event.

        Walks the event's MRO so that listeners registered for a base class
        (e.g. ``Event``) also receive subclass events (e.g. ``UserCreated``).

        Raises:
            RuntimeError: If the bus has not been started as a context manager.
        """
        if not self._tg:
            msg = "EventBus is not started - use 'async with bus:'"
            raise RuntimeError(msg)

        matched: set[Listener] = set()
        for cls in type(event).__mro__:
            if cls in self._listeners:
                matched |= self._listeners[cls]

        if matched:
            logger.debug(
                "Emitting %s to %d listener(s)", type(event).__name__, len(matched)
            )
            for lst in matched:
                self._tg.start_soon(self._call_listener, lst, event)
        else:
            logger.debug("Emitting %s — no listeners registered", type(event).__name__)
