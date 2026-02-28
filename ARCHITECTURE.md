# Architecture

## Overview

litebus is an async event bus with dependency injection for Python 3.12+. Events are typed classes dispatched by type, listeners are async functions decorated with event types, and dependencies are injected by matching parameter names to registered providers. Listeners can emit cascading events back to the bus.

```
┌─────────────────────────────────────────────────────────────┐
│                        EventBus                             │
│                                                             │
│  ┌──────────┐         ┌─────────────────────┐               │
│  │  emit()   │────────▶│     task group       │              │
│  │  (sync)   │         │  tg.start_soon(...)  │              │
│  └──────────┘         └──────┬───┬───┬──────┘               │
│       ▲                      │   │   │                      │
│       │                   ┌──▼─┐┌▼──┐┌▼──┐                  │
│       │                   │ L1 ││L2 ││L3 │                  │
│       │                   └──┬─┘└─┬─┘└─┬─┘                  │
│       │                      │    │    │                     │
│       │   cascading emit()───┘    │    │                     │
│       │◀──────────────────────────┘    │                     │
│       │                                │                     │
│  ┌────▼────────────────────────────────▼───────────────────┐ │
│  │              Dependency Injection (_resolve)             │ │
│  │  annotation is EventBus → inject bus (self-injection)   │ │
│  │  annotation matches event type → inject event           │ │
│  │  parameter name matches DI key → resolve provider       │ │
│  └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

## Dispatch Flow

```
User code                     EventBus internals
─────────                     ──────────────────

bus.emit(UserCreated(...))
    │
    ▼
type(event) = UserCreated
    │
    ▼
_listeners[UserCreated] → {on_user_created, on_audit}
    │
    ▼
tg.start_soon(_call_listener, lst, event)   ← sync, non-blocking
    │                                          spawns task directly
    ▼
    ·  ·  ·  (control returns to caller immediately)  ·  ·  ·
                                              │
                                  ┌───────────▼──────────┐
                                  │  _build_kwargs(fn, event)│
                                  │                          │
                                  │  get_type_hints(fn) →    │
                                  │    bus: EventBus         │
                                  │    → inject self         │
                                  │    event: UserCreated    │
                                  │    → inject event obj    │
                                  │    db: Database          │
                                  │    → _resolve("db")      │
                                  │    logger: Logger        │
                                  │    → _resolve("logger")  │
                                  └───────────┬──────────┘
                                              │
                                      await fn(**kwargs)
                                              │
                                      fn may call bus.emit()
                                      → spawns more tasks
                                        in the same group
```

## Cascading Events

Listeners receive the bus via self-injection (parameter annotated as `EventBus`) and can call `bus.emit()` to fire follow-up events. This works because `emit()` calls `tg.start_soon()` on the same task group, and anyio's `TaskGroup.__aexit__` waits for **all** tasks — including those spawned by other tasks during shutdown.

```
bus.emit(OrderPlaced(...))
    │
    ▼
tg.start_soon(on_order)
    │
    ▼
on_order calls bus.emit(OrderProcessed(...))
    │
    ▼
tg.start_soon(on_order_processed)   ← same task group
    │
    ▼
tg.__aexit__ waits for both on_order AND on_order_processed
```

## Graceful Shutdown

All emitted events — including cascading events — are guaranteed to be fully handled before `__aexit__` returns. The task group naturally provides this:

```
async with bus:              __aenter__                    __aexit__
─────────────                ──────────                    ─────────

                     tg = create_task_group()      tg.__aexit__():
                     await tg.__aenter__()           waits for ALL tasks
                                                     including cascaded ones
                                                         │
                                                         ▼
                                                     ✓ all events handled
```

## Why anyio

**Backend-agnostic concurrency.** anyio is a thin abstraction over asyncio and trio. The bus works on both runtimes without any code changes. Users call `anyio.run(main)` or `trio.run(main)` — same bus code either way.

**`create_task_group()` — structured concurrency.** A single task group owns all listener executions. `emit()` calls `tg.start_soon()` to spawn each listener as a concurrent task. Cascading events spawn additional tasks in the same group, and the group waits for everything before exiting. Unlike raw `asyncio.create_task()`, task groups guarantee no orphaned tasks.

**Key property for cascading events:** anyio's task group keeps `_active = True` while child tasks are running during `__aexit__`, so `start_soon()` called from within a running task (e.g., a listener emitting another event) is accepted and the group waits for those new tasks too.

## Dependency Injection

`_build_kwargs` inspects each listener's signature using `typing.get_type_hints(fn)`:

- If a parameter's **type annotation** is `EventBus`, it receives the bus itself (self-injection for cascading events)
- If a parameter's **type annotation** matches the event (via `isinstance`), it receives the event object
- If a parameter's **name** matches a registered `Provide` key, it is resolved recursively via `_resolve`
- `_resolve` detects circular dependencies by tracking a `frozenset` of names currently being resolved
- `Provide` supports optional caching (`use_cache=True`) — the factory runs once and the result is reused for subsequent calls
