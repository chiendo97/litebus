import inspect
from collections.abc import AsyncGenerator, Callable, Coroutine, Generator
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from typing import cast, final


@final
class Provide:
    """Wraps a callable as a dependency provider."""

    dependency: Callable[..., object]
    _exit_stack: AsyncExitStack

    def __init__(self, dependency: Callable[..., object]) -> None:
        self.dependency = dependency
        self._exit_stack = AsyncExitStack()

    async def __call__(self, **kwargs: object) -> object:
        if inspect.isasyncgenfunction(self.dependency):
            async_gen_fn = cast(
                Callable[..., AsyncGenerator[object, None]], self.dependency
            )
            cm = asynccontextmanager(async_gen_fn)(**kwargs)
            return await self._exit_stack.enter_async_context(cm)
        if inspect.isgeneratorfunction(self.dependency):
            gen_fn = cast(Callable[..., Generator[object, None, None]], self.dependency)
            cm = contextmanager(gen_fn)(**kwargs)
            return self._exit_stack.enter_context(cm)
        if inspect.iscoroutinefunction(self.dependency):
            coro_fn = cast(
                Callable[..., Coroutine[object, object, object]], self.dependency
            )
            return await coro_fn(**kwargs)
        return self.dependency(**kwargs)

    async def aclose(self) -> None:
        """Clean up all active context managers."""
        await self._exit_stack.aclose()
