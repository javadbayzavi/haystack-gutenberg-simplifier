PY := .myenv/bin/python
PIP := .myenv/bin/pip

.PHONY: install lint typecheck test test-network check clean

install:
	$(PIP) install -e ".[dev]"

lint:
	$(PY) -m ruff check src tests pipelines
	$(PY) -m ruff format --check src tests pipelines

format:
	$(PY) -m ruff format src tests pipelines
	$(PY) -m ruff check --fix src tests pipelines

typecheck:
	$(PY) -m mypy

test:
	$(PY) -m pytest -q

# Hits the real Gutenberg site. Kept out of the default suite on purpose.
test-network:
	$(PY) -m pytest -q -m network

check: lint typecheck test

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	find . -name __pycache__ -type d -not -path "./.myenv/*" -exec rm -rf {} +
