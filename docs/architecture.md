# Technical Architecture

## Overview

litebus is a standalone proof-of-concept library that extracts two core patterns from [Litestar](https://github.com/litestar-org/litestar) — **dependency injection** and **event listeners** — into a minimal, independent package. It demonstrates that these patterns can work outside a full web framework, requiring only `anyio` as a runtime dependency.

The library provides:

- **Name-based dependency injection** with recursive resolution and optional caching
- **Async event bus** with fire-and-forget semantics and concurrent listener execution
- **Automatic DI wiring** into event listeners, so listeners declare what they need by parameter name

## Module Layout

```
litebus/
├── __init__.py      # Public exports: EventBus, EventListener, Provide, listener
├── _di.py           # Dependency injection primitives (Provide)
├── _listener.py     # Event listener decorator (EventListener, listener alias)
└── _bus.py          # Event bus core (EventBus) — ties DI and listeners together
```

All modules use leading-underscore naming to signal they are internal. The public API is re-exported through `__init__.py`.

### `_di.py` — Dependency Injection

Defines `Provide`, a wrapper around a callable (factory function) that can resolve it on demand, with optional caching.

### `_listener.py` — Event Listeners

Defines `EventListener`, a decorator that tags a callable with one or more event IDs it subscribes to. `listener` is a convenience alias.

### `_bus.py` — Event Bus

Defines `EventBus`, the orchestrator that connects listeners to events and resolves dependencies when invoking them. Uses anyio memory streams and task groups for async dispatch.

## Dependency Injection

### `Provide` class

`Provide` wraps any callable (sync or async) as a named dependency provider.

```python
@final
class Provide:
    __slots__ = ("_value", "dependency", "use_cache")
```

**Constructor parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `dependency` | `Callable[..., Any]` | required | Factory function that produces the dependency value |
| `use_cache` | `bool` | `False` | If `True`, the resolved value is cached after the first call |

**Internal state:**

- `_value` — starts as a `_MISSING` sentinel; stores the cached result when `use_cache=True`

### Resolution (`Provide.__call__`)

When called, `Provide` executes the factory:

1. If `use_cache=True` and `_value` is not `_MISSING`, return the cached value immediately
2. Detect whether the factory is async via `inspect.iscoroutinefunction()`
3. Call the factory (awaiting if async), passing through any `**kwargs`
4. If `use_cache=True`, store the result in `_value`
5. Return the result

### Recursive Resolution (`EventBus._resolve`)

The bus resolves dependencies recursively. When a dependency's factory itself has parameters that match other registered dependency names, those are resolved first:

```
_resolve("audit")
  → inspect get_audit signature → needs "db", "logger"
    → _resolve("db")   → calls get_db()   → returns Database
    → _resolve("logger") → calls get_logger() → returns Logger
  → calls get_audit(db=..., logger=...) → returns AuditService
```

**Circular dependency detection:** `_resolve` tracks a `frozenset` of names currently being resolved. If a name appears twice in the chain, it raises:

```
RuntimeError("Circular dependency: db -> logger -> db")
```

### Signature Introspection

Both `_resolve` and `_build_kwargs` use `inspect.signature()` to discover what parameters a callable expects. This is the sole mechanism for wiring — there are no decorators, type annotations, or registration calls needed beyond naming parameters to match dependency keys.

## Event System

### `EventListener` class

```python
@final
class EventListener:
    __slots__ = ("event_ids", "fn")
```

**Constructed in two phases:**

1. `EventListener(*event_ids: str)` — stores event IDs as a `frozenset[str]`, sets `fn = None`
2. `__call__(fn)` — stores the decorated function, returns `self`

This two-phase construction enables the `@listener("event_id")` decorator pattern. The `listener` alias is simply `listener = EventListener`.

**Hashability:** `EventListener` implements `__hash__` combining `event_ids` and `fn`, allowing listeners to be stored in sets.

### `EventBus` class

```python
@final
class EventBus:
    __slots__ = ("_dependencies", "_exit_stack", "_listeners", "_send_stream")
```

**Constructor parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `listeners` | `list[EventListener] \| None` | `None` | Listeners to register |
| `dependencies` | `dict[str, Provide] \| None` | `None` | Named dependency providers |

During construction, each listener is indexed under every event ID it subscribes to, using a `defaultdict[str, set[EventListener]]`.

### Stream Plumbing

The bus uses anyio's `create_memory_object_stream` for async dispatch:

```
emit()  ──send_nowait──►  MemoryObjectStream  ──async for──►  _worker()
                              (unbounded)                        │
                                                        create_task_group
                                                           │    │    │
                                                     listener listener listener
```

**`__aenter__`** sets up the runtime:

1. Creates an `AsyncExitStack` to manage resource lifetimes
2. Opens an unbounded memory stream (`max_buffer_size=math.inf`)
3. Creates a task group and schedules `_worker` on it
4. Registers the send stream and task group with the exit stack

**`__aexit__`** tears down cleanly:

1. Closes the exit stack (which closes the send stream, causing the worker to drain and exit)
2. Nulls out `_exit_stack` and `_send_stream`

### `emit()` — Fire-and-Forget

```python
def emit(self, event_id: str, **kwargs: Any) -> None
```

`emit` is **synchronous** — it does not `await`. It places `(listener, kwargs)` tuples onto the send stream via `send_nowait()`. The worker picks these up asynchronously. If the bus is not started, it raises `RuntimeError`.

### Worker and Listener Execution

The `_worker` coroutine:

1. Iterates the receive stream (`async for lst, kwargs in receive_stream`)
2. For each item, spawns `_call_listener(lst, kwargs)` in the task group

`_call_listener`:

1. Guards against `lst.fn is None`
2. Builds kwargs via `_build_kwargs` (see below)
3. Calls the listener (awaiting if async, direct call if sync)
4. Catches **all** exceptions and logs them — listeners never crash the bus

## DI + Events Integration

The bridge between DI and events is `_build_kwargs`:

```python
async def _build_kwargs(self, fn, emit_kwargs) -> dict[str, Any]:
```

For each parameter in the listener's signature:

1. **Check `emit_kwargs` first** — values passed to `emit()` take priority
2. **Check `_dependencies`** — if a registered dependency name matches, resolve it via `_resolve()`
3. **Skip otherwise** — the parameter is left unbound (must have a default or be optional)

This means a listener like:

```python
@listener("user_created")
async def on_user_created(data: dict, db: Database, logger: Logger) -> None: ...
```

...receives `data` from `emit("user_created", data={...})` and `db`/`logger` from registered dependencies.

### Full Emit-to-Listener Flow

```
bus.emit("user_created", data={"name": "Alice"})
  │
  ├─ Look up listeners for "user_created"
  ├─ For each listener: send_nowait((listener, {"data": {"name": "Alice"}}))
  │
  ▼ (async, in worker task group)
  │
  _call_listener(on_user_created, {"data": {"name": "Alice"}})
    │
    ├─ _build_kwargs(on_user_created.fn, emit_kwargs)
    │   ├─ "data" → found in emit_kwargs → use it
    │   ├─ "db"   → found in dependencies → _resolve("db") → get_db()
    │   └─ "logger" → found in dependencies → _resolve("logger") → get_logger()
    │
    ├─ await on_user_created(data=..., db=..., logger=...)
    │
    └─ catch & log any exception
```

## Key Design Decisions

### Name-based resolution (no type-based)

Dependencies are matched by **parameter name**, not by type annotation. If your listener has a parameter called `db`, it matches the dependency registered as `"db"`. This is simpler than Litestar's type-based approach and avoids the need for a type registry.

### Recursive resolution (no topological batching)

Dependencies are resolved on-demand via recursive descent rather than pre-computing a topological order. This keeps the implementation simple and means new dependencies can be added without recomputing a dependency graph. The tradeoff is that without caching, the same dependency may be resolved multiple times in a single call.

### Error isolation per listener

Each listener runs in its own task within the task group. If one listener raises an exception, it is caught and logged — other listeners and the bus itself continue operating. This prevents a single buggy listener from taking down the entire event system.

### `@final` on all classes

All three public classes (`Provide`, `EventListener`, `EventBus`) are marked `@final`. This signals that the classes are not designed for inheritance and their behavior should not be overridden. Combined with `__slots__`, this keeps the classes lean and predictable.

### Unbounded stream buffer

The memory stream uses `max_buffer_size=math.inf`, making `emit()` always non-blocking. This is a deliberate choice for fire-and-forget semantics — the caller never waits for listeners to execute. The tradeoff is that a burst of events can grow memory usage without backpressure.

## Comparison with Litestar

| Aspect | Litestar | litebus |
|--------|----------|---------|
| DI resolution | Type-based with `Provide` and `Dependency` | Name-based with `Provide` only |
| DI scope | Request, app, and custom scopes | Single scope (bus lifetime) |
| Caching | Per-scope caching | Optional per-`Provide` caching |
| Dependency graph | Pre-computed at startup | Recursive on-demand resolution |
| Event dispatch | Plugin-based event emitters | Built-in `EventBus` with memory streams |
| Listener registration | Via plugin hooks | Direct `@listener` decorator |
| Async runtime | ASGI server (uvicorn, etc.) | anyio (standalone) |
| Transport | In-process or configurable backends | In-process memory streams only |
| Error handling | Framework-level exception handlers | Per-listener catch + log |
| Circular deps | Detected at startup | Detected at resolution time |
