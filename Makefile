.PHONY: lint format test benchmark

lint:
	uv run ruff check ./litebus
	uv run basedpyright ./litebus
	uv run black --check ./litebus
	uv run isort --check ./litebus

format:
	uv run black .
	uv run isort .

test:
	uv run pytest tests

benchmark:
	uv run pytest tests/benchmark.py --benchmark-only
