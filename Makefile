.PHONY: install dev test format lint

install:
	python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

dev:
	.venv/bin/uvicorn app.main:app --reload --port 8000

test:
	.venv/bin/pytest -q

format:
	.venv/bin/ruff format . && .venv/bin/ruff check --fix .

lint:
	.venv/bin/ruff check .
