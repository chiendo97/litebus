# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

litebus -- a standalone async event bus with dependency injection for Python 3.12+. Built on anyio.

## Commands

```bash
# Install dependencies (uses uv)
uv sync

# Lint
uv run ruff check .
uv run basedpyright

# Format
uv run black .
uv run isort .

# Run tests
uv run pytest tests

# Run benchmarks (requires pytest-benchmark)
uv run pytest tests/benchmark.py --benchmark-only

# Run example
uv run python example.py
```

There is also a `Makefile` with targets: `lint`, `format`, `test`, `benchmark`.

## Architecture

Four modules under `litebus/`, all re-exported from `__init__.py`:

- **`_types.py`** -- `Event` base class that all events must extend, and `Listener` protocol used internally by the bus.

- **`_listener.py`** -- `EventListener[T]` (aliased as `listener`): generic decorator that binds an async callable to one or more event *types*. Stores `event_types: tuple[type[T], ...]` and the wrapped `fn`. Supports optional `wrappers` applied around the listener at execution time. Implements `__hash__`/`__eq__` based on `(event_types, fn)`. When wrappers are provided, the original function is stored as `fn` and the wrapped version as `executor`.

- **`_di.py`** -- `Provide`: wraps a factory callable as a dependency provider. Supports sync functions, async functions, sync generators, and async generators. Generator-based providers are context-managed via an external `AsyncExitStack`, which means they receive exceptions for rollback on error. Sub-dependencies of a provider are resolved by inspecting its signature parameters. No caching -- each call gets a fresh instance.

- **`_bus.py`** -- `EventBus`: the core. Used as an async context manager (`async with bus:`). On enter it creates an anyio `TaskGroup`. `emit(event)` is fire-and-forget (non-async) -- it walks the event's MRO so that listeners registered for a base class (e.g. `Event`) also receive subclass events. For each matching listener it calls `task_group.start_soon()` to schedule concurrent execution. Each listener call gets its own `AsyncExitStack` for per-call DI lifecycle scoping (generators are cleaned up after each call, not globally). Dependency resolution is recursive (`_resolve`) with circular-dependency detection. Optional `max_concurrency` parameter uses `CapacityLimiter` to throttle concurrent listener calls. Listener exceptions propagate via anyio `ExceptionGroup` -- no silent swallowing.

**Dispatch flow:** `emit(event)` -> walk MRO to find matching listeners -> for each: `task_group.start_soon(_call_listener)` -> create per-call `AsyncExitStack` -> resolve DI params via `_build_kwargs` -> call listener (via `executor` if wrappers exist, else `fn`).

**Key design decisions:**
- MRO-based dispatch: listeners for base `Event` also receive subclass events.
- Per-call DI lifecycle: generator providers are scoped to individual listener calls, enabling rollback on error.
- No silent exception swallowing: listener exceptions propagate via `ExceptionGroup`.
- No dependency caching: each listener call gets fresh dependency instances.
- Self-injection: listeners can request `bus: EventBus` to emit cascading events.

## Conventions

- Strict typing with `basedpyright` in `all` mode (see `pyproject.toml`)
- Ruff with `select = ["ALL"]` minus documented exclusions
- Black + isort for formatting
- Use `@final` on all concrete classes
- Use `Annotated` pattern for typed CLI options (per user preference)
