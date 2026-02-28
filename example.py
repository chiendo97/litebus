"""Usage example: typed event listeners with dependency injection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import anyio

from litebus import EventBus, Provide, listener

# --- events (typed payloads) ---


@dataclass(frozen=True, slots=True)
class UserCreated:
    name: str
    email: str


@dataclass(frozen=True, slots=True)
class OrderPlaced:
    item: str
    qty: int


# --- services (the things we inject) ---


class Database:
    async def save(self, data: dict[str, Any]) -> None:
        print(f"  [DB] saved: {data}")


class Logger:
    def info(self, msg: str) -> None:
        print(f"  [LOG] {msg}")


async def get_db() -> Database:
    return Database()


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


@listener(OrderPlaced)
async def on_order(event: OrderPlaced, logger: Logger) -> None:
    logger.info(f"order placed: {event.item} x{event.qty}")


# --- run ---


async def main() -> None:
    bus = EventBus(
        listeners=[on_user_created, on_audit, on_order],
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

    # all events are fully handled here


anyio.run(main)
