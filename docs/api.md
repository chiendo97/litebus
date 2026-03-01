# API Reference

## `Event`

Base class that all events must extend. Events are typed payloads dispatched through the bus.

```python
from litebus import Event

class UserCreated(Event):
    def __init__(self, name: str, email: str) -> None:
        self.name = name
        self.email = email
```

Events can also use `@dataclass` for concise definitions:

```python
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class OrderPlaced(Event):
    item: str
    qty: int
```

### MRO-Based Hierarchy

Events form a class hierarchy. Listeners registered for a parent class receive all subclass events:

```python
@listener(Event)
async def on_any(event: Event) -> None:
    # receives UserCreated, OrderPlaced, and any other Event subclass
    ...
```

## `Provide`

Wraps a callable as a dependency provider.

```python
from litebus import Provide

Provide(dependency)
```

### Constructor

| Parameter | Type | Description |
|-----------|------|-------------|
| `dependency` | `Callable[..., object]` | Factory function (sync, async, sync generator, or async generator) |

### Supported Factory Types

**Sync function:**

```python
def get_logger() -> Logger:
    return Logger()

Provide(get_logger)
```

**Async function:**

```python
async def get_audit(db: Database, logger: Logger) -> AuditService:
    return AuditService(db, logger)

Provide(get_audit)  # sub-dependencies (db, logger) resolved automatically
```

**Sync generator** (lifecycle-managed):

```python
def get_session() -> Generator[Session, None, None]:
    session = Session()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    else:
        session.commit()
    finally:
        session.close()

Provide(get_session)
```

**Async generator** (lifecycle-managed):

```python
async def get_db() -> AsyncGenerator[Database]:
    db = Database()
    try:
        yield db
    except Exception:
        db.rollback()
        raise              # must re-raise after catching
    else:
        db.commit()
    finally:
        db.close()

Provide(get_db)
```

### Generator Lifecycle

Generator-based providers are entered on a per-call `AsyncExitStack`. The generator's lifecycle is scoped to a single listener call:

- **On success:** the `else` block runs (e.g., commit)
- **On error:** the `except` block runs (e.g., rollback). Must re-raise.
- **Always:** the `finally` block runs (e.g., close connection)

### Recursive Sub-Dependencies

When a factory has parameters matching other registered dependency names, they are resolved automatically:

```python
bus = EventBus(
    dependencies={
        "db": Provide(get_db),
        "logger": Provide(get_logger),
        "audit": Provide(get_audit),  # needs db and logger -- resolved automatically
    },
)
```

## `EventListener` / `listener`

Decorator that marks an async callable as a typed event listener.

`listener` is a convenience alias for `EventListener`.

```python
from litebus import listener  # or EventListener

@listener(UserCreated)
async def on_user_created(event: UserCreated, db: Database) -> None:
    await db.save({"name": event.name})
```

### Constructor

```python
EventListener(*event_types: type[T], wrappers: list[Wrapper] | None = None)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `*event_types` | `type[T]` | One or more event classes this listener subscribes to |
| `wrappers` | `list[Wrapper] \| None` | Optional callables applied around the listener at execution time |

### Behavior

- Used as a two-phase decorator: `EventListener(*event_types)(fn)`
- The decorated function **must be async** (`async def`). Sync functions raise `TypeError`.
- The listener's parameter names and type annotations determine what gets injected (see [Parameter Resolution](#parameter-resolution))
- Implements `__hash__` and `__eq__` based on `(event_types, fn)` for set storage

### Multiple Event Types

A single listener can subscribe to multiple event types:

```python
@listener(UserCreated, UserUpdated)
async def on_user_change(event: UserCreated | UserUpdated) -> None:
    ...
```

### Wrappers

Wrappers are callables applied around the listener at decoration time. They wrap the original function, which is stored as `fn`, while the wrapped version becomes `executor`:

```python
def timed(fn):
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = await fn(*args, **kwargs)
        print(f"{fn.__name__} took {time.perf_counter() - start:.4f}s")
        return result
    return wrapper

@listener(OrderPlaced, wrappers=[timed])
async def on_order(event: OrderPlaced) -> None:
    ...
```

## `EventBus`

Core event dispatcher. Connects listeners to events and resolves dependencies when invoking them. Must be used as an async context manager.

```python
from litebus import EventBus

async with EventBus(listeners=..., dependencies=...) as bus:
    bus.emit(event)
```

### Constructor

```python
EventBus(
    listeners: Sequence[Listener] | None = None,
    dependencies: dict[str, Provide] | None = None,
    *,
    max_concurrency: int | None = None,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `listeners` | `Sequence[Listener] \| None` | `None` | Event listeners to register |
| `dependencies` | `dict[str, Provide] \| None` | `None` | Named dependency providers |
| `max_concurrency` | `int \| None` | `None` | Maximum concurrent listener calls (keyword-only) |

### Async Context Manager

The bus **must** be entered as an async context manager before emitting events:

```python
async with bus:
    bus.emit(UserCreated(name="Alice", email="alice@example.com"))
# all listeners have completed (or raised) at this point
```

On entry (`__aenter__`):
- Creates an anyio `TaskGroup` for concurrent listener execution

On exit (`__aexit__`):
- Awaits all in-flight listener tasks via the `TaskGroup`
- If any listener raised, exceptions surface as `BaseExceptionGroup`

The bus can be entered multiple times (reusable context manager).

### `emit(event)`

Fire-and-forget typed event emission.

```python
bus.emit(event: Event) -> None
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `event` | `Event` | A typed event instance |

**Behavior:**

- **Synchronous** -- does not `await`, returns immediately
- Walks the event's MRO to find all matching listeners (hierarchy dispatch)
- Schedules each matching listener via `task_group.start_soon()`
- Raises `RuntimeError` if called outside the `async with bus:` block
- If no listeners match, the call is a no-op

### Error Handling

Listener exceptions propagate via anyio `ExceptionGroup`. They are **not** caught or logged silently:

```python
try:
    async with bus:
        bus.emit(event)
except* ValueError as eg:
    # handle ValueError from listeners
    ...
```

### Concurrency Control

Use `max_concurrency` to limit how many listeners execute simultaneously:

```python
bus = EventBus(listeners=[...], max_concurrency=10)
```

This uses anyio's `CapacityLimiter` under the hood.

### Self-Injection

Listeners can request the bus itself to emit cascading events:

```python
@listener(OrderPlaced)
async def on_order(event: OrderPlaced, bus: EventBus) -> None:
    # cascading: emit a new event from within a listener
    bus.emit(OrderProcessed(item=event.item, status="confirmed"))
```

## Parameter Resolution

When a listener is invoked, the bus builds its keyword arguments by inspecting the function signature. Each parameter is resolved in this order:

### 1. EventBus self-injection

If a parameter's type annotation is `EventBus`, the bus injects itself:

```python
@listener(MyEvent)
async def handler(event: MyEvent, bus: EventBus) -> None:
    bus.emit(AnotherEvent())  # cascading
```

### 2. Event matching

If a parameter's type annotation matches the emitted event (via `isinstance`), the event is injected:

```python
@listener(UserCreated)
async def handler(event: UserCreated) -> None:
    print(event.name)
```

### 3. Registered dependencies

If a parameter name matches a key in the `dependencies` dict, the corresponding `Provide` is invoked:

```python
bus = EventBus(
    dependencies={"db": Provide(get_db)},
    ...
)

@listener(MyEvent)
async def handler(event: MyEvent, db: Database) -> None:
    # "db" resolved from Provide(get_db)
    ...
```

### 4. Recursive sub-dependencies

When a dependency's factory has parameters matching other registered dependency names, those are resolved recursively:

```python
async def get_audit(db: Database, logger: Logger) -> AuditService:
    return AuditService(db, logger)

bus = EventBus(
    dependencies={
        "db": Provide(get_db),
        "logger": Provide(get_logger),
        "audit": Provide(get_audit),  # db and logger resolved automatically
    },
)
```

### 5. Circular dependency detection

If dependency A requires B and B requires A, the bus raises `RuntimeError` at resolution time:

```
RuntimeError("Circular dependency: a -> b -> a")
```

### 6. Unmatched parameters

Parameters that don't match any of the above are skipped. The function must provide defaults for these, or a `TypeError` will occur at call time.

### Resolution Summary

```
For each parameter in listener signature:
  1. annotation is EventBus?  -> inject self (bus)
  2. annotation matches event? -> inject event
  3. name in dependencies?     -> resolve via Provide (recursive)
  4. none of the above?        -> skip (must have default)
```

## Complete Example

```python
import anyio
from collections.abc import AsyncGenerator
from litebus import Event, EventBus, Provide, listener

class UserCreated(Event):
    def __init__(self, name: str) -> None:
        self.name = name

class Database:
    async def save(self, data: dict) -> None: ...

async def get_db() -> AsyncGenerator[Database]:
    db = Database()
    try:
        yield db
    except Exception:
        print("rollback")
        raise
    else:
        print("commit")
    finally:
        print("closed")

@listener(UserCreated)
async def on_user(event: UserCreated, db: Database) -> None:
    await db.save({"name": event.name})

async def main() -> None:
    bus = EventBus(
        listeners=[on_user],
        dependencies={"db": Provide(get_db)},
    )
    async with bus:
        bus.emit(UserCreated(name="Alice"))

anyio.run(main)
```
