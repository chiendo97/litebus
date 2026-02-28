import inspect
from collections.abc import Callable
from typing import Any, final

_MISSING = object()


@final
class Provide:
    """Wraps a callable as a dependency provider."""

    dependency: Callable[..., Any]
    use_cache: bool
    _value: object

    def __init__(
        self,
        dependency: Callable[..., Any],
        *,
        use_cache: bool = False,
    ) -> None:
        self.dependency = dependency
        self.use_cache = use_cache
        self._value = _MISSING

    async def __call__(self, **kwargs: Any) -> Any:
        if self.use_cache and self._value is not _MISSING:
            return self._value

        if inspect.iscoroutinefunction(self.dependency):
            value = await self.dependency(**kwargs)
        else:
            value = self.dependency(**kwargs)

        if self.use_cache:
            self._value = value

        return value
