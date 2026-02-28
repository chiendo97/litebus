import inspect
import logging
import math
from collections import defaultdict
from collections.abc import Callable
from contextlib import AsyncExitStack
from types import TracebackType
from typing import Any, Self, final

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

from ._di import Provide
from ._listener import EventListener

type _Item = tuple[EventListener, dict[str, Any]]

logger = logging.getLogger(__name__)


@final
class EventBus:
    """Event emitter with dependency injection for listeners."""

    __slots__ = ("_dependencies", "_exit_stack", "_listeners", "_send_stream")

    _listeners: defaultdict[str, set[EventListener]]
    _dependencies: dict[str, Provide]
    _send_stream: MemoryObjectSendStream[_Item] | None
    _exit_stack: AsyncExitStack | None

    def __init__(
        self,
        listeners: list[EventListener] | None = None,
        dependencies: dict[str, Provide] | None = None,
    ) -> None:
        self._listeners = defaultdict(set)
        self._dependencies = dependencies or {}
        self._send_stream = None
        self._exit_stack = None

        for lst in listeners or []:
            for event_id in lst.event_ids:
                self._listeners[event_id].add(lst)

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
        emit_kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        """Build the full kwargs dict for a listener call."""
        sig = inspect.signature(fn)
        kwargs: dict[str, Any] = {}

        for name in sig.parameters:
            if name in emit_kwargs:
                kwargs[name] = emit_kwargs[name]
            elif name in self._dependencies:
                kwargs[name] = await self._resolve(name)

        return kwargs

    # -- listener execution --

    async def _call_listener(
        self,
        lst: EventListener,
        emit_kwargs: dict[str, Any],
    ) -> None:
        try:
            if lst.fn is None:
                return
            kwargs = await self._build_kwargs(lst.fn, emit_kwargs)
            if inspect.iscoroutinefunction(lst.fn):
                await lst.fn(**kwargs)
            else:
                lst.fn(**kwargs)
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
            async for lst, kwargs in receive_stream:
                tg.start_soon(self._call_listener, lst, kwargs)

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

    def emit(self, event_id: str, **kwargs: Any) -> None:
        """Fire-and-forget: emit an event with payload kwargs."""
        if not self._send_stream:
            msg = "EventBus is not started - use 'async with bus:'"
            raise RuntimeError(msg)

        if listeners := self._listeners.get(event_id):
            for lst in listeners:
                self._send_stream.send_nowait((lst, kwargs))
