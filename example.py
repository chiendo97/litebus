"""Usage example: typed event listeners with dependency injection."""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import Callable, Coroutine
from typing import cast, final

import anyio

from litebus import Event, EventBus, Provide, listener

logging.basicConfig()
logging.getLogger("litebus").setLevel(logging.DEBUG)

# --- events (typed payloads) ---


@final
class UserCreated(Event):
    def __init__(self, name: str, email: str) -> None:
        self.name = name
        self.email = email


@final
class OrderPlaced(Event):
    def __init__(self, item: str, qty: int) -> None:
        self.item = item
        self.qty = qty


@final
class OrderProcessed(Event):
    def __init__(self, item: str, qty: int, status: str) -> None:
        self.item = item
        self.qty = qty
        self.status = status


# --- services (the things we inject) ---


class Database:
    async def save(self, data: dict[str, object]) -> None:
        print(f"  [DB] saved: {data}")


class Logger:
    def info(self, msg: str) -> None:
        print(f"  [LOG] {msg}")


async def get_db():
    db = Database()
    yield db


def get_logger() -> Logger:
    return Logger()


# --- dependencies can depend on other dependencies ---


class AuditService:
    db: Database
    logger: Logger

    def __init__(self, db: Database, logger: Logger) -> None:
        self.db = db
        self.logger = logger

    async def record(self, action: str) -> None:
        self.logger.info(f"audit: {action}")
        await self.db.save({"audit": action})


async def get_audit(db: Database, logger: Logger) -> AuditService:
    return AuditService(db, logger)


# --- wrappers: decorators applied around the listener at execution time ---
# For real-world use, swap with prefect.flow / prefect.task:
#   from prefect import flow
#   @listener(OrderPlaced, wrappers=[flow])


def timed(
    fn: Callable[..., object],
) -> Callable[..., Coroutine[object, object, None]]:
    """Wrapper that logs execution time of an async listener."""

    @functools.wraps(fn)
    async def wrapper(*args: object, **kwargs: object) -> None:
        start = time.perf_counter()
        _ = await cast(Callable[..., Coroutine[object, object, None]], fn)(
            *args, **kwargs
        )
        elapsed = time.perf_counter() - start
        print(f"  [TIMER] {fn.__name__} took {elapsed:.4f}s")

    return wrapper


# --- event listeners: event param matched by type, others resolved via DI ---


@listener(UserCreated)
async def on_user_created(
    event: UserCreated,
    db: Database,
    logger: Logger,
) -> None:
    logger.info(f"user created: {event}")
    await db.save({"name": event.name, "email": event.email})


@listener(UserCreated)
async def on_audit(event: UserCreated, audit: AuditService) -> None:
    await audit.record(f"user_created: {event.name}")


@listener(OrderPlaced, wrappers=[timed])
async def on_order(event: OrderPlaced, logger: Logger, bus: EventBus) -> None:
    logger.info(f"order placed: {event.item} x{event.qty}")
    bus.emit(OrderProcessed(item=event.item, qty=event.qty, status="confirmed"))


@listener(OrderProcessed)
async def on_order_processed(event: OrderProcessed, logger: Logger) -> None:
    logger.info(f"order processed: {event.item} x{event.qty} [{event.status}]")


@listener(Event)
async def on_error(event: Event, logger: Logger) -> None:
    logger.info(f"error handling event: {event}")


# --- run ---


async def main() -> None:
    bus = EventBus(
        listeners=[on_user_created, on_audit, on_order, on_order_processed],
        dependencies={
            "db": Provide(get_db),
            "logger": Provide(get_logger),
            "audit": Provide(get_audit),  # depends on db + logger
        },
    )

    async with bus:
        print("--- emit UserCreated ---")
        bus.emit(UserCreated(name="Alice", email="alice@example.com"))

        print("--- emit OrderPlaced ---")
        bus.emit(OrderPlaced(item="Widget", qty=3))

    # all events are fully handled here (including cascaded OrderProcessed)


anyio.run(main)
