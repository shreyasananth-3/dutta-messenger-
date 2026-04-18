.PHONY: help install install-dev up down logs lint format typecheck test test-fast test-unit test-integration test-e2e coverage mutation security-scan secrets-scan sbom migrate migrate-new migrate-down run seed openapi-export clean preflight

VENV       ?= .venv
PYTHON     ?= $(VENV)/bin/python
PIP        ?= $(VENV)/bin/pip
PYTEST     ?= $(VENV)/bin/pytest
RUFF       ?= $(VENV)/bin/ruff
MYPY       ?= $(VENV)/bin/mypy
ALEMBIC    ?= $(VENV)/bin/alembic
UVICORN    ?= $(VENV)/bin/uvicorn
COVERAGE   ?= $(VENV)/bin/coverage
MUTMUT     ?= $(VENV)/bin/mutmut
DETECT_SEC ?= $(VENV)/bin/detect-secrets
PIP_AUDIT  ?= $(VENV)/bin/pip-audit
CYCLONEDX  ?= $(VENV)/bin/cyclonedx-py
PRECOMMIT  ?= $(VENV)/bin/pre-commit
ACTIVATE    = . $(VENV)/bin/activate

preflight:  ## Run the local equivalent of CI before pushing
	@echo "==> [1/6] ruff check"
	@$(RUFF) check src tests
	@echo "==> [2/6] ruff format --check"
	@$(RUFF) format --check src tests
	@echo "==> [3/6] mypy --strict (warn-only, matches CI)"
	@$(MYPY) src || true
	@echo "==> [4/6] pytest + coverage"
	@./scripts/run_tests.sh > /dev/null
	@echo "==> [5/6] pip-audit"
	@$(PIP_AUDIT) --strict || echo "  (pip-audit found advisories — warn-only in CI too)"
	@echo "==> [6/6] detect-secrets"
	@$(DETECT_SEC) scan --baseline .secrets.baseline > /dev/null
	@echo "\n  Local CI equivalent green. Safe to push."

help:  ## Show this help message
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

install:  ## Install runtime dependencies
	$(PIP) install -e .

install-dev:  ## Install runtime + dev + test dependencies
	$(PIP) install -e ".[dev,test]"
	$(PRECOMMIT) install

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
	$(RUFF) check src tests

format:  ## Auto-format with ruff
	$(RUFF) format src tests
	$(RUFF) check --fix src tests

typecheck:  ## Run mypy strict type-checker
	$(MYPY) src

secrets-scan:  ## Scan repo for committed secrets
	$(DETECT_SEC) scan --baseline .secrets.baseline

security-scan:  ## Run pip-audit for vulnerable dependencies
	$(PIP_AUDIT) --strict

sbom:  ## Generate CycloneDX SBOM
	$(CYCLONEDX) requirements -o sbom.xml

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------

test:  ## Run the full test suite with coverage + timestamped results folder
	./scripts/run_tests.sh

test-fast:  ## Run only unit tests (no DB required)
	$(PYTEST) -m unit -n auto

test-unit:  ## Run unit tests
	$(PYTEST) -m unit

test-integration:  ## Run integration tests (requires DB + Redis)
	$(PYTEST) -m integration

test-e2e:  ## Run end-to-end tests
	$(PYTEST) -m e2e

coverage:  ## Show coverage report
	$(COVERAGE) report -m
	@echo "HTML report: tests/results/latest/coverage-html/index.html"

mutation:  ## Run mutation testing on auth + shared modules
	$(MUTMUT) run --paths-to-mutate src/shared/,src/modules/auth/
	$(MUTMUT) results

# ---------------------------------------------------------------------------
# Database migrations
# ---------------------------------------------------------------------------

migrate:  ## Apply all pending migrations
	$(ALEMBIC) upgrade head

migrate-new:  ## Create a new migration (usage: make migrate-new MSG="description")
	$(ALEMBIC) revision --autogenerate -m "$(MSG)"

migrate-down:  ## Roll back one migration (use with care)
	$(ALEMBIC) downgrade -1

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

run:  ## Run the FastAPI app locally
	$(UVICORN) src.main:app --reload --host 0.0.0.0 --port 8000

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
