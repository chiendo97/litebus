import inspect
from collections.abc import Callable, Coroutine
from typing import Self, cast, final, overload, override

type AsyncFn = Callable[..., Coroutine[object, object, None]]


@final
class EventListener[T]:
    """Decorator that marks an async callable as a typed event listener."""

    event_types: tuple[type[T], ...]
    fn: AsyncFn | None
    __wrapped__: AsyncFn | None

    def __init__(self, *event_types: type[T]) -> None:
        self.event_types = event_types
        self.fn = None
        self.__wrapped__ = None

    @overload
    def __call__(self, fn: AsyncFn, /) -> Self: ...

    @overload
    def __call__(self, **kwargs: object) -> Coroutine[object, object, None]: ...

    def __call__(
        self, *args: object, **kwargs: object
    ) -> Self | Coroutine[object, object, None]:
        if self.fn is None:
            fn = cast(AsyncFn, args[0])
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
