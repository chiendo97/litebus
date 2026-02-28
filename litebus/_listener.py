import inspect
from collections.abc import Callable, Coroutine
from typing import Any, final, override

type AsyncCallable = Callable[..., Coroutine[Any, Any, Any]]


@final
class EventListener[T]:
    """Decorator that marks an async callable as a typed event listener."""

    __slots__ = ("event_types", "fn")

    event_types: tuple[type[T], ...]
    fn: AsyncCallable | None

    def __init__(self, *event_types: type[T]) -> None:
        self.event_types = event_types
        self.fn = None

    def __call__(self, fn: AsyncCallable) -> "EventListener[T]":
        if not inspect.iscoroutinefunction(fn):
            msg = f"Event listener must be an async function, got {fn!r}"
            raise TypeError(msg)
        self.fn = fn
        return self

    @override
    def __hash__(self) -> int:
        return hash((self.event_types, self.fn))


listener = EventListener
