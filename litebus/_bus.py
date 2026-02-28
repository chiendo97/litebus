import inspect
import logging
import math
import typing
from collections import defaultdict
from collections.abc import Callable
from contextlib import AsyncExitStack
from types import TracebackType
from typing import Any, Self, final

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

from ._di import Provide
from ._listener import EventListener

type _Item = tuple[EventListener[Any], Any]

logger = logging.getLogger(__name__)


def _is_event_param(annotation: type, event: object) -> bool:
    """Check if a parameter annotation matches the event type."""
    try:
        return isinstance(event, annotation)
    except TypeError:
        return False


@final
class EventBus:
    """Event emitter with dependency injection for listeners."""

    __slots__ = ("_dependencies", "_exit_stack", "_listeners", "_send_stream")

    _listeners: defaultdict[type, set[EventListener[Any]]]
    _dependencies: dict[str, Provide]
    _send_stream: MemoryObjectSendStream[_Item] | None
    _exit_stack: AsyncExitStack | None

    def __init__(
        self,
        listeners: list[EventListener[Any]] | None = None,
        dependencies: dict[str, Provide] | None = None,
    ) -> None:
        self._listeners = defaultdict(set)
        self._dependencies = dependencies or {}
        self._send_stream = None
        self._exit_stack = None

        for lst in listeners or []:
            for event_type in lst.event_types:
                self._listeners[event_type].add(lst)

    # -- dependency resolution --

    async def _resolve(
        self,
        name: str,
        _resolving: frozenset[str] | None = None,
    ) -> Any:
        """Recursively resolve a single dependency by name."""
        resolving = _resolving or frozenset()
        if name in resolving:
            msg = f"Circular dependency: {' -> '.join(resolving)} -> {name}"
            raise RuntimeError(msg)

        provider = self._dependencies[name]
        resolving = resolving | {name}

        # resolve sub-dependencies of this provider
        sig = inspect.signature(provider.dependency)
        sub_kwargs: dict[str, Any] = {}
        for param_name in sig.parameters:
            if param_name in self._dependencies:
                sub_kwargs[param_name] = await self._resolve(param_name, resolving)

        return await provider(**sub_kwargs)

    async def _build_kwargs(
        self,
        fn: Callable[..., Any],
        event: Any,
    ) -> dict[str, Any]:
        """Build the full kwargs dict for a listener call."""
        sig = inspect.signature(fn)
        hints = typing.get_type_hints(fn)
        kwargs: dict[str, Any] = {}

        for name in sig.parameters:
            annotation = hints.get(name)
            if isinstance(annotation, type) and _is_event_param(annotation, event):
                kwargs[name] = event
            elif name in self._dependencies:
                kwargs[name] = await self._resolve(name)

        return kwargs

    # -- listener execution --

    async def _call_listener(
        self,
        lst: EventListener[Any],
        event: Any,
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

    # -- worker / stream plumbing --

    async def _worker(
        self,
        receive_stream: MemoryObjectReceiveStream[_Item],
    ) -> None:
        async with receive_stream, anyio.create_task_group() as tg:
            async for lst, event in receive_stream:
                tg.start_soon(self._call_listener, lst, event)

    async def __aenter__(self) -> Self:
        self._exit_stack = AsyncExitStack()
        send_stream, receive_stream = anyio.create_memory_object_stream[_Item](
            max_buffer_size=math.inf,  # type: ignore[arg-type]
        )
        self._send_stream = send_stream
        tg = anyio.create_task_group()
        _ = await self._exit_stack.enter_async_context(tg)
        _ = await self._exit_stack.enter_async_context(send_stream)
        tg.start_soon(self._worker, receive_stream)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._exit_stack:
            _ = await self._exit_stack.__aexit__(exc_type, exc_val, exc_tb)
        self._exit_stack = None
        self._send_stream = None

    # -- public API --

    def emit(self, event: object) -> None:
        """Fire-and-forget: emit a typed event."""
        if not self._send_stream:
            msg = "EventBus is not started - use 'async with bus:'"
            raise RuntimeError(msg)

        event_type = type(event)
        if listeners := self._listeners.get(event_type):
            for lst in listeners:
                self._send_stream.send_nowait((lst, event))
