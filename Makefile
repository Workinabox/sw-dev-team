.PHONY: sync lint fmt typecheck test coverage build check

# The modules the coverage gate applies to: pure logic with no credential or
# network dependency. This mirrors the backend, which gates wiab-core at 98% and
# leaves the I/O crates ungated — a threshold over code that cannot be exercised
# without an API key or a database would just price a permanent gap into itself.
# Kept on one line: Make turns a line continuation into a space, and coverage
# splits --include on commas without trimming, so a wrapped list silently
# matches nothing but the first entry.
COVERAGE_CORE = src/wiab_team/models/*,src/wiab_team/config.py,src/wiab_team/errors.py,src/wiab_team/vcs/*,src/wiab_team/graph/state.py,src/wiab_team/graph/routing.py,src/wiab_team/graph/prompts/*,src/wiab_team/worker/*

COVERAGE_MIN = 98

sync:
	uv sync --all-extras --dev

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests

fmt:
	uv run ruff format src tests
	uv run ruff check --fix src tests

typecheck:
	uv run mypy

test:
	uv run pytest -m "not live and not postgres"

# Reports the whole package for visibility, then gates only the core scope.
coverage:
	uv run pytest -m "not live and not postgres" --cov=wiab_team --cov-report=term-missing
	uv run coverage report --include="$(COVERAGE_CORE)" --fail-under=$(COVERAGE_MIN)

build:
	uv build

check: lint typecheck test
