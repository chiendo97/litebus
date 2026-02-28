from collections.abc import Callable, Coroutine
from typing import Protocol


class Event:
    """Base class that all events must extend."""


class Listener(Protocol):
    """Structural type for event listeners, erasing the generic parameter."""

    @property
    def event_types(self) -> tuple[type[Event], ...]: ...

    @property
    def fn(self) -> Callable[..., Coroutine[object, object, None]] | None: ...

    @property
    def executor(self) -> Callable[..., Coroutine[object, object, None]] | None: ...
