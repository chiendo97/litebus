# Technical Architecture

## Overview

litebus is a standalone async event bus with dependency injection for Python 3.12+, built on [anyio](https://github.com/agronholm/anyio). It extracts two core patterns -- **typed event dispatch** and **name-based dependency injection** -- into a minimal, independent package.

The library provides:

- **Typed event dispatch** with MRO-based hierarchy matching
- **Name-based dependency injection** with recursive resolution
- **Per-call DI lifecycle** scoping via `AsyncExitStack` for generator-based providers
- **Fire-and-forget semantics** with concurrent listener execution via anyio `TaskGroup`

## Module Layout

```
litebus/
├── __init__.py      # Public exports: Event, EventBus, EventListener, Provide, listener
├── _types.py        # Event base class and Listener protocol
├── _di.py           # Dependency injection primitives (Provide)
├── _listener.py     # Event listener decorator (EventListener, listener alias)
└── _bus.py          # Event bus core (EventBus) — ties DI and listeners together
```

All modules use leading-underscore naming to signal they are internal. The public API is re-exported through `__init__.py`.

### `_types.py` -- Event and Listener Protocol

Defines `Event`, the base class that all events must extend, and `Listener`, a structural protocol used internally by the bus to erase the generic parameter from `EventListener[T]`.

### `_di.py` -- Dependency Injection

Defines `Provide`, a wrapper around a callable (factory function) that resolves it on demand. Supports four factory types: sync functions, async functions, sync generators, and async generators. Generator-based providers are context-managed via an external `AsyncExitStack`, which means they receive exceptions for rollback on error. No caching -- each call gets a fresh instance.

### `_listener.py` -- Event Listeners

Defines `EventListener[T]`, a generic decorator that binds an async callable to one or more event types. Stores `event_types: tuple[type[T], ...]` and the wrapped `fn`. Supports optional `wrappers` applied around the listener at execution time. Implements `__hash__`/`__eq__` based on `(event_types, fn)`. When wrappers are provided, the original function is stored as `fn` and the wrapped version as `executor`. `listener` is a convenience alias.

### `_bus.py` -- Event Bus

Defines `EventBus`, the core orchestrator. Used as an async context manager (`async with bus:`). On enter it creates an anyio `TaskGroup`. `emit(event)` is fire-and-forget (non-async) -- it walks the event's MRO so that listeners registered for a base class (e.g. `Event`) also receive subclass events. For each matching listener it calls `task_group.start_soon()` to schedule concurrent execution. Each listener call gets its own `AsyncExitStack` for per-call DI lifecycle scoping. Optional `max_concurrency` parameter uses `CapacityLimiter` to throttle concurrent listener calls.

## Dependency Injection

### `Provide` class

`Provide` wraps any callable as a dependency provider.

```python
@final
class Provide:
    dependency: Callable[..., object]
```

**Constructor:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `dependency` | `Callable[..., object]` | Factory function that produces the dependency value |

### Factory Types

`Provide` detects the factory type via `inspect` and handles each accordingly:

| Factory Type | Detection | Behavior |
|-------------|-----------|----------|
| Async generator | `isasyncgenfunction()` | Wrapped with `asynccontextmanager`, entered on the call's `AsyncExitStack` |
| Sync generator | `isgeneratorfunction()` | Wrapped with `contextmanager`, entered on the call's `AsyncExitStack` |
| Async function | `iscoroutinefunction()` | Awaited directly |
| Sync function | (default) | Called directly |

### Generator-Based Providers

Generator providers enable setup/teardown lifecycle per listener call:

```python
async def get_db() -> AsyncGenerator[Database]:
    db = Database()
    try:
        yield db          # setup: provide the dependency
    except Exception:
        db.rollback()     # rollback on listener error
        raise             # must re-raise (contextmanager requirement)
    else:
        db.commit()       # commit on success
    finally:
        db.close()        # cleanup always runs
```

The generator is entered on a per-call `AsyncExitStack`. When the listener completes (or raises), the stack unwinds, and the generator receives the exception (if any) via `contextmanager.__exit__`. This enables transaction-like rollback patterns.

**Important:** Generators that catch exceptions must re-raise them. If a generator catches an exception without re-raising, `contextmanager` suppresses it, and the exception will not propagate to the caller.

### Recursive Resolution (`EventBus._resolve`)

The bus resolves dependencies recursively. When a dependency's factory has parameters matching other registered dependency names, those are resolved first:

```
_resolve("audit")
  -> inspect get_audit signature -> needs "db", "logger"
    -> _resolve("db")     -> calls get_db()     -> returns Database
    -> _resolve("logger") -> calls get_logger() -> returns Logger
  -> calls get_audit(db=..., logger=...) -> returns AuditService
```

**Circular dependency detection:** `_resolve` tracks a `frozenset` of names currently being resolved. If a name appears twice in the chain, it raises:

```
RuntimeError("Circular dependency: db -> logger -> db")
```

### Signature Introspection

Both `_resolve` and `_build_kwargs` use `inspect.signature()` to discover what parameters a callable expects. This is the sole mechanism for wiring -- there are no decorators, type annotations, or registration calls needed beyond naming parameters to match dependency keys.

## Event System

### `EventListener[T]` class

```python
@final
class EventListener[T]:
    event_types: tuple[type[T], ...]
    fn: AsyncFn | None
    executor: AsyncFn | None
```

**Constructed in two phases:**

1. `EventListener(*event_types: type[T], wrappers=None)` -- stores event types, sets `fn = None`
2. `__call__(fn)` -- stores the decorated async function, applies wrappers, returns `self`

This two-phase construction enables the `@listener(EventType)` decorator pattern.

**Wrappers:** When `wrappers` are provided, the original function is stored as `fn` and the wrapped version as `executor`. During execution, the bus calls `executor` if it exists, otherwise `fn`.

**Hashability:** `EventListener` implements `__hash__` and `__eq__` based on `(event_types, fn)`, allowing listeners to be stored in sets and deduplicated.

### `EventBus` class

```python
@final
class EventBus:
    _listeners: defaultdict[type, set[Listener]]
    _dependencies: dict[str, Provide]
    _tg: anyio.abc.TaskGroup | None
    _limiter: anyio.CapacityLimiter | None
```

**Constructor:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `listeners` | `Sequence[Listener] \| None` | `None` | Event listeners to register |
| `dependencies` | `dict[str, Provide] \| None` | `None` | Named dependency providers |
| `max_concurrency` | `int \| None` | `None` | Maximum concurrent listener calls (keyword-only) |

During construction, each listener is indexed under every event type it subscribes to, using a `defaultdict[type, set[Listener]]`.

### Dispatch Flow

```
bus.emit(UserCreated(name="Alice", email="alice@example.com"))
  |
  +-- Walk type(event).__mro__ to find matching listeners
  +-- Deduplicate via set union
  +-- For each matched listener: task_group.start_soon(_call_listener)
       |
       +-- Create per-call AsyncExitStack
       +-- _build_kwargs(fn, event, call_stack)
       |     +-- "event" param: matched by type annotation (isinstance check)
       |     +-- "bus" param: matched by EventBus type annotation (self-injection)
       |     +-- other params: matched by name against registered dependencies
       |
       +-- Call executor (if wrappers) or fn
       +-- AsyncExitStack unwinds (generator providers cleaned up)
```

### MRO-Based Hierarchy Dispatch

`emit()` walks the event's Method Resolution Order (`type(event).__mro__`). This means a listener registered for the base `Event` class receives all events:

```python
@listener(Event)
async def on_any_event(event: Event) -> None:
    # fires for UserCreated, OrderPlaced, and any other Event subclass
    ...
```

### Parameter Resolution in `_build_kwargs`

For each parameter in the listener's signature:

1. **Type annotation is `EventBus`** -- inject the bus itself (enables cascading events)
2. **Type annotation matches the event** (`isinstance` check) -- inject the event
3. **Name matches a registered dependency** -- resolve via `_resolve()`
4. **No match** -- skip (must have a default or `TypeError` at call time)

### Per-Call DI Lifecycle

Each listener call creates its own `AsyncExitStack`:

```python
async with AsyncExitStack() as call_stack:
    kwargs = await self._build_kwargs(lst.fn, event, call_stack)
    await executor(**kwargs)
```

Generator-based providers enter their context on `call_stack`. When the `async with` block exits:
- **On success:** generators reach their `else` block (commit)
- **On exception:** generators receive the exception in their `except` block (rollback)
- **Always:** generators run their `finally` block (cleanup)

This scoping ensures each listener call gets fresh dependencies and proper lifecycle management.

### Concurrency Control

When `max_concurrency` is set, the bus uses an anyio `CapacityLimiter` to throttle concurrent listener calls. Each listener acquires the limiter before execution and releases it in a `finally` block.

### Error Handling

Listener exceptions are **not swallowed**. They propagate through the anyio `TaskGroup`, which surfaces them as a `BaseExceptionGroup` when the `async with bus:` block exits:

```python
with pytest.raises(BaseExceptionGroup) as exc_info:
    async with bus:
        bus.emit(FailingEvent())
```

This design ensures errors are never silently lost and enables generator-based providers to perform rollback logic.

## Key Design Decisions

### Typed events (not string IDs)

Events are Python classes extending `Event`, not string identifiers. This provides type safety, IDE support, and enables MRO-based hierarchy dispatch.

### MRO-based dispatch

Listeners for base `Event` receive all subclass events. This enables catch-all patterns without explicit subscription to every event type.

### Name-based DI resolution (not type-based)

Dependencies are matched by **parameter name**, not by type annotation. If your listener has a parameter called `db`, it matches the dependency registered as `"db"`. This is simpler than type-based approaches and avoids the need for a type registry.

### Per-call DI lifecycle (not bus-scoped)

Generator-based providers are scoped to individual listener calls via `AsyncExitStack`, not to the bus lifetime. This enables transaction-like patterns where each listener call gets its own database session that commits on success and rolls back on error.

### No dependency caching

Each listener call gets fresh dependency instances. There is no `use_cache` option. This simplifies the mental model and avoids shared mutable state between concurrent listeners.

### No silent exception swallowing

Listener exceptions propagate via anyio `ExceptionGroup`. This ensures errors are visible and enables generator-based providers to receive exceptions for rollback. Previous designs caught and logged exceptions, which silently lost errors.

### Self-injection

Listeners can request `bus: EventBus` as a parameter to emit cascading events from within a listener.

### `@final` on all concrete classes

All public classes (`Provide`, `EventListener`, `EventBus`) are marked `@final`. This signals that the classes are not designed for inheritance. Combined with explicit attribute declarations, this keeps the classes lean and predictable.
