SHELL := /bin/bash
.DEFAULT_GOAL := help

FIXTURE ?= contracts/fixtures/01_rename/change_set.json
OUT ?= out/report.json
FIXES_DIR ?= out/fixes

.PHONY: help setup test lint fmt typecheck check demo doctor stubs env-up env-down seed clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## Create the venv and install dev + all extras
	uv sync --all-extras --group dev
	uv run pre-commit install

test: ## Run the test suite
	uv run pytest

lint: ## Lint with ruff
	uv run ruff check .
	uv run ruff format --check .

fmt: ## Auto-format with ruff
	uv run ruff format .
	uv run ruff check --fix .

typecheck: ## Type-check with mypy strict
	uv run mypy

check: lint typecheck test ## Everything CI runs

demo: ## Run the pipeline against a golden fixture (exits non-zero while stubs remain)
	@mkdir -p $(FIXES_DIR)
	-uv run blast-radius analyze --change-set $(FIXTURE) --out $(OUT) --fixes-dir $(FIXES_DIR)
	@echo
	@uv run blast-radius stubs

doctor: ## Verify the DataHub read and write paths before depending on them
	-uv run blast-radius doctor

stubs: ## List every unimplemented stub, grouped by owner
	uv run blast-radius stubs

env-up: ## Start the local DataHub quickstart (OWNER B)
	./env/quickstart.sh up

env-down: ## Stop the local DataHub quickstart (OWNER B)
	./env/quickstart.sh down

seed: ## Ingest the demo dbt project and plant the adversarial description (OWNER B)
	uv run python env/seed_demo.py

clean: ## Remove caches and generated output
	rm -rf out .pytest_cache .mypy_cache .ruff_cache
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
