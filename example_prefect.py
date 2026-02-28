"""Example: event listeners running as Prefect flows and tasks."""

from __future__ import annotations

import logging
from typing import final

import anyio
from prefect import flow, task

from litebus import Event, EventBus, Provide, listener

logging.basicConfig()
logging.getLogger("litebus").setLevel(logging.DEBUG)

# --- events ---


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


# --- services ---


class Logger:
    def info(self, msg: str) -> None:
        print(f"  [LOG] {msg}")


def get_logger() -> Logger:
    return Logger()


# --- listeners wrapped with Prefect flow / task ---


@listener(OrderPlaced, wrappers=[flow])
async def on_order(event: OrderPlaced, logger: Logger, bus: EventBus) -> None:
    """Runs as a Prefect flow — visible in the Prefect UI."""
    logger.info(f"order placed: {event.item} x{event.qty}")
    bus.emit(OrderProcessed(item=event.item, qty=event.qty, status="confirmed"))


@listener(OrderProcessed, wrappers=[task])
async def on_order_processed(event: OrderProcessed, logger: Logger) -> None:
    """Runs as a Prefect task — tracked inside a flow or standalone."""
    logger.info(f"order processed: {event.item} x{event.qty} [{event.status}]")


# --- run ---


async def main() -> None:
    bus = EventBus(
        listeners=[on_order, on_order_processed],
        dependencies={
            "logger": Provide(get_logger),
        },
    )

    async with bus:
        print("--- emit OrderPlaced ---")
        bus.emit(OrderPlaced(item="Widget", qty=3))


anyio.run(main)
