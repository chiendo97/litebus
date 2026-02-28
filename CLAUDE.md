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

No test suite exists yet.

## Architecture

Three modules under `litebus/`, all re-exported from `__init__.py`:

- **`_listener.py`** — `EventListener` (aliased as `listener`): decorator that binds a callable to one or more event IDs. Stores `event_ids: frozenset[str]` and the wrapped `fn`.

- **`_di.py`** — `Provide`: wraps a factory callable as a dependency provider. Supports both sync and async factories, optional caching (`use_cache=True`). Sub-dependencies of a provider are resolved by inspecting its signature parameters.

- **`_bus.py`** — `EventBus`: the core. Used as an async context manager (`async with bus:`). Internally creates an anyio `MemoryObjectStream` and a worker task group. `emit()` is fire-and-forget (non-async) — it pushes `(listener, kwargs)` tuples into the stream. The worker picks them up and calls each listener concurrently via the task group. Dependency resolution is recursive (`_resolve`) with circular-dependency detection.

**Dispatch flow:** `emit()` → send stream → worker → for each matching listener: resolve DI params + merge emit kwargs → call listener.

## Conventions

- Strict typing with `basedpyright` in `all` mode (see `pyproject.toml` for suppressed reports)
- Ruff with `select = ["ALL"]` minus documented exclusions
- Black + isort for formatting
- Use `@final` on all concrete classes
- Use `Annotated` pattern for typed CLI options (per user preference)
