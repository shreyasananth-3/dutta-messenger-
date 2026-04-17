.PHONY: help install install-dev up down logs lint format typecheck test test-fast test-unit test-integration test-e2e coverage mutation security-scan secrets-scan sbom migrate migrate-new migrate-down run seed openapi-export clean

PYTHON ?= python3
PIP ?= pip
VENV ?= .venv
ACTIVATE = . $(VENV)/bin/activate

help:  ## Show this help message
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

install:  ## Install runtime dependencies
	$(PIP) install -e .

install-dev:  ## Install runtime + dev + test dependencies
	$(PIP) install -e ".[dev,test]"
	pre-commit install

up:  ## Start Postgres, Redis, MinIO via docker-compose
	docker compose up -d

down:  ## Stop all docker services
	docker compose down

logs:  ## Tail docker service logs
	docker compose logs -f --tail=100

# ---------------------------------------------------------------------------
# Code quality
# ---------------------------------------------------------------------------

lint:  ## Run ruff linter
	ruff check src tests

format:  ## Auto-format with ruff
	ruff format src tests
	ruff check --fix src tests

typecheck:  ## Run mypy strict type-checker
	mypy src

secrets-scan:  ## Scan repo for committed secrets
	detect-secrets scan --baseline .secrets.baseline

security-scan:  ## Run pip-audit for vulnerable dependencies
	pip-audit --strict

sbom:  ## Generate CycloneDX SBOM
	cyclonedx-py requirements -o sbom.xml

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------

test:  ## Run the full test suite with coverage + timestamped results folder
	./scripts/run_tests.sh

test-fast:  ## Run only unit tests (no DB required)
	pytest -m unit -n auto

test-unit:  ## Run unit tests
	pytest -m unit

test-integration:  ## Run integration tests (requires DB + Redis)
	pytest -m integration

test-e2e:  ## Run end-to-end tests
	pytest -m e2e

coverage:  ## Show coverage report
	coverage report -m
	@echo "HTML report: tests/results/latest/coverage-html/index.html"

mutation:  ## Run mutation testing on auth + shared modules
	mutmut run --paths-to-mutate src/shared/,src/modules/auth/
	mutmut results

# ---------------------------------------------------------------------------
# Database migrations
# ---------------------------------------------------------------------------

migrate:  ## Apply all pending migrations
	alembic upgrade head

migrate-new:  ## Create a new migration (usage: make migrate-new MSG="description")
	alembic revision --autogenerate -m "$(MSG)"

migrate-down:  ## Roll back one migration (use with care)
	alembic downgrade -1

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

run:  ## Run the FastAPI app locally
	uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

seed:  ## Seed the database with baseline data (admin, institution, group)
	$(PYTHON) scripts/seed.py

openapi-export:  ## Export OpenAPI spec to docs/ui-contract/openapi.json
	$(PYTHON) scripts/export_openapi.py

# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------

clean:  ## Remove build artifacts and caches
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf .coverage htmlcov dist build *.egg-info
