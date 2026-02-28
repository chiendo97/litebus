import inspect
from collections.abc import Callable, Coroutine
from typing import Self, cast, final, overload, override

type AsyncFn = Callable[..., Coroutine[object, object, None]]
type Wrapper = Callable[[Callable[..., object]], Callable[..., object]]


def _find_async_fn(obj: object) -> AsyncFn | None:
    """Traverse __wrapped__ chain to find the original async function."""
    current: object = obj
    while current is not None:
        if inspect.iscoroutinefunction(current):
            return cast(AsyncFn, current)
        current = getattr(current, "__wrapped__", None)
    return None


@final
class EventListener[T]:
    """Decorator that marks an async callable as a typed event listener."""

    event_types: tuple[type[T], ...]
    fn: AsyncFn | None
    executor: AsyncFn | None
    __wrapped__: AsyncFn | None

    def __init__(
        self, *event_types: type[T], wrappers: list[Wrapper] | None = None
    ) -> None:
        self.event_types = event_types
        self.fn = None
        self.executor = None
        self.__wrapped__ = None
        self._wrappers = tuple(wrappers or ())

    @overload
    def __call__(self, fn: Callable[..., object], /) -> Self: ...

    @overload
    def __call__(self, **kwargs: object) -> Coroutine[object, object, None]: ...

    def __call__(
        self, *args: object, **kwargs: object
    ) -> Self | Coroutine[object, object, None]:
        if self.fn is None:
            target = args[0]
            original = _find_async_fn(target)
            if original is None:
                msg = f"Event listener must wrap an async function, got {target!r}"
                raise TypeError(msg)

            self.fn = original
            self.__wrapped__ = original

            # Build executor: if target is already wrapped (e.g. by @flow),
            # start the chain from it; otherwise start from the raw function.
            wrapped: Callable[..., object] = cast(Callable[..., object], target)
            for wrapper in self._wrappers:
                wrapped = wrapper(wrapped)
            self.executor = cast(AsyncFn, wrapped)

            # Copy function metadata so outer decorators (@flow, @task) work.
            for attr in ("__module__", "__name__", "__qualname__", "__doc__"):
                val = getattr(original, attr, None)
                if val is not None:
                    object.__setattr__(self, attr, val)
            return self

        # Delegate to fn directly (not executor) to avoid infinite loops
        # when an outer decorator (@flow) wraps this listener: the bus calls
        # executor(=Flow) → Flow calls listener.__call__ → fn (no cycle).
        return self.fn(*args, **kwargs)

    @override
    def __hash__(self) -> int:
        return hash((self.event_types, self.fn))


listener = EventListener
