import inspect
from collections.abc import Callable
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from typing import final


@final
class Provide:
    """Wraps a callable as a dependency provider."""

    dependency: Callable
    _exit_stack: AsyncExitStack

    def __init__(self, dependency: Callable) -> None:
        self.dependency = dependency
        self._exit_stack = AsyncExitStack()

    async def __call__(self, **kwargs):
        if inspect.isasyncgenfunction(self.dependency):
            cm = asynccontextmanager(self.dependency)(**kwargs)
            return await self._exit_stack.enter_async_context(cm)
        if inspect.isgeneratorfunction(self.dependency):
            cm = contextmanager(self.dependency)(**kwargs)
            return self._exit_stack.enter_context(cm)
        if inspect.iscoroutinefunction(self.dependency):
            return await self.dependency(**kwargs)
        return self.dependency(**kwargs)

    async def aclose(self) -> None:
        """Clean up all active context managers."""
        await self._exit_stack.aclose()
