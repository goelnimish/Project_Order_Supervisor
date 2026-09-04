SHELL := /bin/sh

ROOT_DIR := $(CURDIR)
FRONTEND_DIR := $(ROOT_DIR)/frontend
BACKEND_DIR := $(ROOT_DIR)/backend
COMPOSE := docker compose --project-directory "$(ROOT_DIR)" --env-file "$(ROOT_DIR)/.env" -f "$(ROOT_DIR)/docker-compose.yml"
STAGE3_PROVIDER ?= ollama
STAGE35_RUN_ID ?=

.PHONY: help install frontend-install backend-install infra-up infra-down infra-logs \
	frontend-dev backend-dev backend-test backend-lint backend-format frontend-lint \
	frontend-build db-migrate temporal-worker temporal-demo stage2-demo \
	ollama-eval stage3-demo stage35-demo temporal-infra-check check

help: ## Show available commands
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-18s %s\n", $$1, $$2}' "$(ROOT_DIR)/Makefile"

install: frontend-install backend-install ## Install all project dependencies

frontend-install: ## Install frontend dependencies with npm
	npm --prefix "$(FRONTEND_DIR)" install

backend-install: ## Sync backend dependencies with uv
	cd "$(BACKEND_DIR)" && uv sync --locked

infra-up: ## Start PostgreSQL and Temporal and wait for them to become healthy
	$(COMPOSE) up -d --wait --wait-timeout 90 postgres temporal

infra-down: ## Stop local infrastructure while retaining named volumes
	$(COMPOSE) down

infra-logs: ## Follow PostgreSQL and Temporal logs
	$(COMPOSE) logs --follow postgres temporal

frontend-dev: ## Start the Next.js development server
	npm --prefix "$(FRONTEND_DIR)" run dev

backend-dev: ## Start the FastAPI development server
	@set -a; \
	. "$(ROOT_DIR)/.env"; \
	set +a; \
	cd "$(BACKEND_DIR)" && uv run --locked uvicorn app.main:app --reload --host "$$API_HOST" --port "$$API_PORT"

backend-test: ## Run backend tests
	cd "$(BACKEND_DIR)" && uv run --locked pytest

backend-lint: ## Check backend and Python demo linting and formatting
	cd "$(BACKEND_DIR)" && uv run --locked ruff check . ../scripts
	cd "$(BACKEND_DIR)" && uv run --locked ruff format --check . ../scripts

backend-format: ## Format backend and Python demo files
	cd "$(BACKEND_DIR)" && uv run --locked ruff format . ../scripts

db-migrate: ## Apply all PostgreSQL business-schema migrations
	cd "$(BACKEND_DIR)" && uv run --locked alembic upgrade head

frontend-lint: ## Run frontend ESLint
	npm --prefix "$(FRONTEND_DIR)" run lint

frontend-build: ## Build the frontend for production
	npm --prefix "$(FRONTEND_DIR)" run build

temporal-worker: ## Run the Temporal Worker
	@set -a; \
	. "$(ROOT_DIR)/.env"; \
	set +a; \
	cd "$(BACKEND_DIR)" && uv run --locked python -m app.temporal.worker

temporal-demo: ## Run the Stage 1 Temporal demonstration
	@set -a; \
	. "$(ROOT_DIR)/.env"; \
	set +a; \
	cd "$(BACKEND_DIR)" && PYTHONPATH="$(BACKEND_DIR)" uv run --locked python "$(ROOT_DIR)/scripts/temporal_stage1_demo.py"

stage2-demo: ## Run the Stage 2 scenario through the FastAPI API
	@set -a; \
	. "$(ROOT_DIR)/.env"; \
	set +a; \
	cd "$(BACKEND_DIR)" && PYTHONPATH="$(BACKEND_DIR)" uv run --locked python "$(ROOT_DIR)/scripts/stage2_api_demo.py"

ollama-eval: ## Evaluate qwen3:1.7b against the five required routing cases
	@set -a; \
	. "$(ROOT_DIR)/.env"; \
	set +a; \
	cd "$(BACKEND_DIR)" && PYTHONPATH="$(BACKEND_DIR)" uv run --locked python "$(ROOT_DIR)/scripts/ollama_supervisor_eval.py"

stage3-demo: ## Run the Stage 3 AI scenario (STAGE3_PROVIDER=ollama|deterministic)
	@set -a; \
	. "$(ROOT_DIR)/.env"; \
	set +a; \
	cd "$(BACKEND_DIR)" && PYTHONPATH="$(BACKEND_DIR)" uv run --locked python "$(ROOT_DIR)/scripts/stage3_ai_demo.py" --provider "$(STAGE3_PROVIDER)"

stage35-demo: ## Read Stage 3.5 analytics (requires STAGE35_RUN_ID=<uuid>)
	@if [ -z "$(STAGE35_RUN_ID)" ]; then \
		echo "STAGE35_RUN_ID is required (use a persisted run UUID)." >&2; \
		exit 2; \
	fi
	cd "$(BACKEND_DIR)" && uv run --locked python "$(ROOT_DIR)/scripts/stage35_analytics_demo.py" --run-id "$(STAGE35_RUN_ID)"

temporal-infra-check: ## Check PostgreSQL, Temporal gRPC, and the Temporal Web UI
	@$(COMPOSE) exec -T postgres pg_isready -U order_supervisor -d order_supervisor
	@$(COMPOSE) exec -T temporal temporal operator cluster health --address 127.0.0.1:7233
	@curl --fail --silent --show-error --output /dev/null http://127.0.0.1:8233/
	@echo "Temporal Web UI is reachable at http://localhost:8233"

check: backend-lint backend-test frontend-lint frontend-build ## Run all tests and checks
