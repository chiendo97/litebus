# API Reference

## `Provide`

Wraps a callable as a named dependency provider.

```python
from litebus import Provide

Provide(dependency, *, use_cache=False)
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `dependency` | `Callable[..., Any]` | required | Factory function (sync or async) that produces the dependency value |
| `use_cache` | `bool` | `False` | Cache the resolved value after first invocation |

### Behavior

- When invoked, calls the factory and returns the result
- Supports both sync and async factories — async factories are awaited automatically
- When `use_cache=True`, the factory is called once; subsequent invocations return the cached value
- Factories can have parameters that reference other registered dependencies (resolved recursively by the bus)

### Example

```python
class Database:
    async def save(self, data: dict) -> None: ...

async def get_db() -> Database:
    return Database()

db_provider = Provide(get_db, use_cache=True)
```

## `EventListener` / `listener`

Decorator that marks a callable as an event listener for one or more event IDs.

`listener` is a convenience alias for `EventListener`.

```python
from litebus import listener  # or EventListener

@listener("event_id")
async def on_event(data: dict) -> None: ...
```

### Constructor

```python
EventListener(*event_ids: str)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `*event_ids` | `str` | One or more event ID strings this listener subscribes to |

### Behavior

- Used as a two-phase decorator: `EventListener(*event_ids)(fn)`
- The decorated function is stored on `listener.fn`
- A listener can subscribe to multiple events:

```python
@listener("user_created", "user_updated")
async def on_user_change(data: dict) -> None: ...
```

- Listeners can be sync or async — the bus handles both
- The listener's parameter names determine what gets injected (see [Dependency Resolution Rules](#dependency-resolution-rules))

### Example

```python
@listener("order_placed")
def on_order(data: dict, logger: Logger) -> None:
    logger.info(f"order: {data}")

@listener("user_created")
async def on_user_created(data: dict, db: Database) -> None:
    await db.save(data)
```

## `EventBus`

Core event dispatcher. Connects listeners to events and resolves dependencies when invoking them. Must be used as an async context manager.

```python
from litebus import EventBus

bus = EventBus(listeners=..., dependencies=...)

async with bus:
    bus.emit("event_id", key=value)
```

### Constructor

```python
EventBus(
    listeners: list[EventListener] | None = None,
    dependencies: dict[str, Provide] | None = None,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `listeners` | `list[EventListener] \| None` | `None` | Event listeners to register |
| `dependencies` | `dict[str, Provide] \| None` | `None` | Named dependency providers |

### Async Context Manager

The bus **must** be entered as an async context manager before emitting events:

```python
async with bus:
    # bus is active, emit() works here
    bus.emit("my_event", data={"key": "value"})
# bus is shut down, streams closed, tasks awaited
```

On entry (`__aenter__`):
- Opens a memory stream for event dispatch
- Starts a background worker task that processes queued events

On exit (`__aexit__`):
- Closes the stream, causing the worker to drain remaining events
- Awaits all in-flight listener tasks

### `emit(event_id, **kwargs)`

Fire-and-forget event emission.

```python
bus.emit(event_id: str, **kwargs: Any) -> None
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `event_id` | `str` | The event identifier to emit |
| `**kwargs` | `Any` | Keyword arguments passed to matching listeners |

**Behavior:**

- **Synchronous** — does not `await`, returns immediately
- Queues events onto an internal stream for async processing
- Raises `RuntimeError` if called outside the `async with bus:` block
- If no listeners are registered for the event ID, the call is a no-op

### Example

```python
async def main() -> None:
    bus = EventBus(
        listeners=[on_user_created, on_audit, on_order],
        dependencies={
            "db": Provide(get_db),
            "logger": Provide(get_logger),
            "audit": Provide(get_audit),
        },
    )

    async with bus:
        bus.emit("user_created", data={"name": "Alice"})
        bus.emit("order_placed", data={"item": "Widget", "qty": 3})
        await anyio.sleep(0.1)  # allow listeners to execute
```

## Dependency Resolution Rules

When a listener is invoked, the bus builds its keyword arguments by inspecting the function signature. Each parameter is resolved using the following priority:

### 1. Emit kwargs take priority

Values passed directly to `emit()` are used first:

```python
bus.emit("user_created", data={"name": "Alice"})
# "data" parameter in any matching listener receives {"name": "Alice"}
```

### 2. Registered dependencies

If a parameter name matches a key in the `dependencies` dict (and was not provided via emit kwargs), the corresponding `Provide` instance is invoked:

```python
bus = EventBus(
    dependencies={"db": Provide(get_db)},
    ...
)

@listener("my_event")
async def handler(db: Database) -> None:
    # "db" resolved from Provide(get_db)
    ...
```

### 3. Recursive sub-dependency resolution

When a dependency's factory has parameters that match other registered dependency names, those are resolved recursively:

```python
async def get_audit(db: Database, logger: Logger) -> AuditService:
    return AuditService(db, logger)

bus = EventBus(
    dependencies={
        "db": Provide(get_db),
        "logger": Provide(get_logger),
        "audit": Provide(get_audit),  # db and logger resolved automatically
    },
    ...
)
```

### 4. Circular dependency detection

If dependency A requires B and B requires A, the bus raises a `RuntimeError` at resolution time:

```python
RuntimeError("Circular dependency: a -> b -> a")
```

The detection tracks the full resolution path and reports it in the error message.

### 5. Unmatched parameters

Parameters that don't match emit kwargs or any registered dependency are skipped. The function must provide defaults for these, or a `TypeError` will occur at call time.

### Resolution Summary

```
For each parameter in listener signature:
  1. emit kwargs?        → use value from emit()
  2. registered dep?     → resolve via Provide (recursive)
  3. neither?            → skip (must have default)
```
