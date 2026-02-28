.PHONY: lint test benchmark

lint:
	uv run ruff check ./litebus
	uv run basedpyright ./litebus
	uv run black --check ./litebus
	uv run isort --check ./litebus

test:
	uv run pytest

benchmark:
	uv run pytest tests/benchmark.py --benchmark-only
