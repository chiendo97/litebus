"""Example: event listeners running as Prefect flows, tasks, and sub-flows.

Uses the ``wrappers`` parameter to integrate with Prefect:

  @listener(OrderPlaced, wrappers=[flow])                          # simple
  @listener(OrderPlaced, wrappers=[lambda fn: flow(fn, name=...)])  # with params

Pipeline in the Prefect UI:

  OrderPlaced ──► [flow] process-order
                    ├── [task] validate-stock
                    ├── [task] reserve-stock
                    └── [sub-flow] process-payment
                          ├── [task] charge-payment
                          └── [task] verify-payment
                                └── emit OrderProcessed

  OrderProcessed ──► [task] send-confirmation
"""

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
    def __init__(self, item: str, qty: int, txn_id: str) -> None:
        self.item = item
        self.qty = qty
        self.txn_id = txn_id


# --- services (injected by the bus) ---


class Inventory:
    async def check(self, item: str, qty: int) -> bool:
        print(f"  [INVENTORY] checking stock: {item} x{qty}")
        return True

    async def reserve(self, item: str, qty: int) -> None:
        print(f"  [INVENTORY] reserved: {item} x{qty}")


class PaymentGateway:
    async def charge(self, item: str, qty: int) -> str:
        print(f"  [PAYMENT] charged for {item} x{qty}")
        return "txn_abc123"

    async def verify(self, txn_id: str) -> bool:
        print(f"  [PAYMENT] verified txn {txn_id}")
        return True


def get_inventory() -> Inventory:
    return Inventory()


def get_payment() -> PaymentGateway:
    return PaymentGateway()


# --- Prefect tasks (called inside the flow listener) ---


@task(name="validate-stock", log_prints=True)
async def validate_stock(inventory: Inventory, item: str, qty: int) -> bool:
    return await inventory.check(item, qty)


@task(name="reserve-stock", log_prints=True)
async def reserve_stock(inventory: Inventory, item: str, qty: int) -> None:
    await inventory.reserve(item, qty)


@task(name="charge-payment", log_prints=True)
async def charge_payment(payment: PaymentGateway, item: str, qty: int) -> str:
    return await payment.charge(item, qty)


@task(name="verify-payment", log_prints=True)
async def verify_payment(payment: PaymentGateway, txn_id: str) -> bool:
    return await payment.verify(txn_id)


# --- sub-flow (called inside the main flow listener) ---


@flow(name="process-payment", description="Charge and verify payment", log_prints=True)
async def process_payment(payment: PaymentGateway, item: str, qty: int) -> str:
    """Sub-flow: charge → verify → return txn_id."""
    txn_id = await charge_payment(payment, item, qty)
    verified = await verify_payment(payment, txn_id)
    if not verified:
        msg = f"Payment verification failed for {txn_id}"
        raise RuntimeError(msg)
    print(f"  payment complete: {txn_id}")
    return txn_id


# --- listeners ---


@listener(
    OrderPlaced,
    wrappers=[
        lambda fn: flow(
            fn,
            name="process-order",
            description="Validate, reserve, and charge",
            log_prints=True,
        )
    ],
)
async def on_order_placed(
    event: OrderPlaced,
    inventory: Inventory,
    payment: PaymentGateway,
    bus: EventBus,
) -> None:
    """Flow: validate → reserve → [sub-flow] payment → emit OrderProcessed."""
    in_stock = await validate_stock(inventory, event.item, event.qty)
    if not in_stock:
        print(f"  order rejected: {event.item} out of stock")
        return

    await reserve_stock(inventory, event.item, event.qty)
    txn_id = await process_payment(payment, event.item, event.qty)

    print(f"  order fulfilled: {event.item} x{event.qty} (txn={txn_id})")
    bus.emit(OrderProcessed(item=event.item, qty=event.qty, txn_id=txn_id))


@listener(
    OrderProcessed,
    wrappers=[lambda fn: task(fn, name="send-confirmation", log_prints=True)],
)
async def on_order_processed(event: OrderProcessed) -> None:
    """Task: send order confirmation."""
    print(f"  confirmation sent: {event.item} x{event.qty} [{event.txn_id}]")


# --- run ---


async def main() -> None:
    bus = EventBus(
        listeners=[on_order_placed, on_order_processed],
        dependencies={
            "inventory": Provide(get_inventory),
            "payment": Provide(get_payment),
        },
    )

    async with bus:
        print("--- emit OrderPlaced ---")
        bus.emit(OrderPlaced(item="Widget", qty=3))


anyio.run(main)
