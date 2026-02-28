from collections.abc import Callable
from typing import Any, final, override


@final
class EventListener:
    """Decorator that marks a callable as an event listener."""

    __slots__ = ("event_ids", "fn")

    event_ids: frozenset[str]
    fn: Callable[..., Any] | None

    def __init__(self, *event_ids: str) -> None:
        self.event_ids = frozenset(event_ids)
        self.fn = None

    def __call__(self, fn: Callable[..., Any]) -> "EventListener":
        self.fn = fn
        return self

    @override
    def __hash__(self) -> int:
        return hash((self.event_ids, self.fn))


listener = EventListener
