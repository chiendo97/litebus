"""Usage example: event listeners with dependency injection."""

from __future__ import annotations

from typing import Any

import anyio

from litebus import EventBus, Provide, listener

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

    async def record(self, action: str, data: dict[str, Any]) -> None:
        self.logger.info(f"audit: {action}")
        await self.db.save({"audit": action, **data})


async def get_audit(db: Database, logger: Logger) -> AuditService:
    return AuditService(db, logger)


# --- event listeners: parameters are resolved via DI or emit kwargs ---


@listener("user_created")
async def on_user_created(
    data: dict[str, Any],
    db: Database,
    logger: Logger,
) -> None:
    logger.info(f"user created: {data}")
    await db.save(data)


@listener("user_created")
async def on_audit(data: dict[str, Any], audit: AuditService) -> None:
    await audit.record("user_created", data)


@listener("order_placed")
def on_order(data: dict[str, Any], logger: Logger) -> None:
    logger.info(f"order placed: {data}")


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
        print("--- emit user_created ---")
        bus.emit("user_created", data={"name": "Alice", "email": "alice@example.com"})

        print("--- emit order_placed ---")
        bus.emit("order_placed", data={"item": "Widget", "qty": 3})

        # give listeners time to run
        await anyio.sleep(0.1)


anyio.run(main)
