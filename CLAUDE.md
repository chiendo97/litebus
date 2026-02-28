# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

litebus — a standalone async event bus with dependency injection for Python 3.12+. Built on anyio.

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

# Run example
uv run python example.py
```

Benchmarks live under `tests/benchmark.py` (requires `pytest-benchmark`).

## Architecture

Five modules under `litebus/`, all re-exported from `__init__.py`:

- **`_types.py`** -- `Event` base class that all events must extend, and `Listener` protocol used internally by the bus.

- **`_listener.py`** -- `EventListener[T]` (aliased as `listener`): generic decorator that binds an async callable to one or more event *types*. Stores `event_types: tuple[type[T], ...]` and the wrapped `fn`. Supports optional `wrappers` applied around the listener at execution time.

- **`_di.py`** -- `Provide`: wraps a factory callable as a dependency provider. Supports sync functions, async functions, sync generators, and async generators (context-managed via `AsyncExitStack`). Optional caching (`use_cache=True`) avoids re-invoking the factory after the first call. Sub-dependencies of a provider are resolved by inspecting its signature parameters.

- **`_bus.py`** -- `EventBus`: the core. Used as an async context manager (`async with bus:`). On enter it creates an anyio `TaskGroup`. `emit(event)` is fire-and-forget (non-async) -- for each listener registered to the event's type, it calls `task_group.start_soon()` to schedule the listener concurrently. Dependency resolution is recursive (`_resolve`) with circular-dependency detection. On exit, the task group awaits all spawned tasks and providers are closed.

**Dispatch flow:** `emit(event)` -> for each matching listener: `task_group.start_soon(_call_listener)` -> resolve DI params via `_build_kwargs` -> call listener.

## Conventions

- Strict typing with `basedpyright` in `all` mode (see `pyproject.toml` for suppressed reports)
- Ruff with `select = ["ALL"]` minus documented exclusions
- Black + isort for formatting
- Use `@final` on all concrete classes
- Use `Annotated` pattern for typed CLI options (per user preference)
