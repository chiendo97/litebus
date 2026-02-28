# Copyright 2026 chiendo97

import inspect
import logging
import typing
from collections import defaultdict
from collections.abc import Callable
from types import TracebackType
from typing import Any, Self, final

import anyio
import anyio.abc

from ._di import Provide
from ._listener import EventListener

logger = logging.getLogger(__name__)


class Event:
    """Base class that all events must extend."""


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

    _listeners: defaultdict[type, set[EventListener[Event]]]
    _dependencies: dict[str, Provide]
    _tg: anyio.abc.TaskGroup | None

    def __init__(
        self,
        listeners: list[EventListener[Event]] | None = None,
        dependencies: dict[str, Provide] | None = None,
    ) -> None:
        self._listeners = defaultdict(set)
        self._dependencies = dependencies or {}
        self._tg = None

        for lst in listeners or []:
            for event_type in lst.event_types:
                self._listeners[event_type].add(lst)

    # -- dependency resolution --

    async def _resolve(
        self,
        name: str,
        _resolving: frozenset[str] | None = None,
    ):
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
        sub_kwargs: dict[str, Any] = {}
        for param_name in sig.parameters:
            if param_name in self._dependencies:
                sub_kwargs[param_name] = await self._resolve(param_name, resolving)

        return await provider(**sub_kwargs)

    async def _build_kwargs(
        self,
        fn: Callable[..., object],
        event: Event,
    ) -> dict[str, Any]:
        """Build the full kwargs dict for a listener call.

        Returns:
            Mapping of parameter names to resolved values.
        """
        sig = inspect.signature(fn)
        hints = typing.get_type_hints(fn)
        kwargs: dict[str, Any] = {}

        for name in sig.parameters:
            annotation = hints.get(name)
            if annotation is EventBus:
                kwargs[name] = self
            elif isinstance(annotation, type) and _is_event_param(annotation, event):
                kwargs[name] = event
            elif name in self._dependencies:
                kwargs[name] = await self._resolve(name)

        return kwargs

    # -- listener execution --

    async def _call_listener[T: Event](
        self,
        lst: EventListener[T],
        event: T,
    ) -> None:
        try:
            if lst.fn is None:
                return
            kwargs = await self._build_kwargs(lst.fn, event)
            await lst.fn(**kwargs)
        except Exception:
            logger.exception(
                "Error in listener %s",
                getattr(lst.fn, "__name__", lst.fn),
            )

    # -- context manager --

    async def __aenter__(self) -> Self:
        self._tg = anyio.create_task_group()
        _ = await self._tg.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._tg:
            _ = await self._tg.__aexit__(exc_type, exc_val, exc_tb)
        self._tg = None

    # -- public API --

    def emit(self, event: Event) -> None:
        """Fire-and-forget: emit a typed event.

        Raises:
            RuntimeError: If the bus has not been started as a context manager.
        """
        if not self._tg:
            msg = "EventBus is not started - use 'async with bus:'"
            raise RuntimeError(msg)

        event_type = type(event)
        if listeners := self._listeners.get(event_type):
            for lst in listeners:
                self._tg.start_soon(self._call_listener, lst, event)
