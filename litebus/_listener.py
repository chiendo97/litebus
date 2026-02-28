import inspect
from collections.abc import Callable, Coroutine
from typing import Any, final, override

type AsyncCallable = Callable[..., Coroutine]


@final
class EventListener[T]:
    """Decorator that marks an async callable as a typed event listener."""

    event_types: tuple[type[T], ...]
    fn: AsyncCallable | None
    __wrapped__: AsyncCallable | None

    def __init__(self, *event_types: type[T]) -> None:
        self.event_types = event_types
        self.fn = None
        self.__wrapped__ = None

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if self.fn is None:
            fn: AsyncCallable = args[0]
            if not inspect.iscoroutinefunction(fn):
                msg = f"Event listener must be an async function, got {fn!r}"
                raise TypeError(msg)
            self.fn = fn
            self.__wrapped__ = fn
            return self
        return self.fn(*args, **kwargs)

    @override
    def __hash__(self) -> int:
        return hash((self.event_types, self.fn))


listener = EventListener
