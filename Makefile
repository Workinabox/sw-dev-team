.PHONY: sync lint fmt typecheck test check

sync:
	uv sync --all-extras --dev

lint:
	uv run ruff check src tests

fmt:
	uv run ruff format src tests
	uv run ruff check --fix src tests

typecheck:
	uv run mypy

test:
	uv run pytest -m "not live and not postgres"

check: lint typecheck test
