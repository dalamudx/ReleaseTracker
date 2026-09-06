.PHONY: help install run-backend run-frontend lint test test-backend test-frontend format clean build dbmate-migrate version

# Default target
.DEFAULT_GOAL := help

# Variable definitions
PYTHON = python3
UV = uv
NPM = npm

help: ## Show help information
	@echo "Usage: make [target]"
	@echo ""
	@echo "Available targets:"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install all dependencies (backend and frontend)
	@echo "📦 Installing backend dependencies..."
	cd backend && $(UV) sync --locked --extra dev
	@echo "📦 Installing frontend dependencies..."
	cd frontend && $(NPM) ci

run-backend: ## Run the backend service
	@echo "🚀 Starting backend service..."
	cd backend && $(UV) run uvicorn releasetracker.main:app --host 0.0.0.0 --port 8000 --reload

run-frontend: ## Run the frontend service
	@echo "🚀 Starting frontend service..."
	cd frontend && $(NPM) run dev

dev: ## Run the backend and frontend together (requires make -j2)
	@echo "🚀 Starting the development environment..."
	@$(MAKE) -j2 run-backend run-frontend

lint: ## Check code (backend ruff/black, frontend eslint)
	@echo "🔍 Checking backend code..."
	cd backend && $(UV) run --locked --extra dev ruff check src tests
	cd backend && $(UV) run --locked --extra dev black --check src tests
	@echo "� Checking frontend code..."
	cd frontend && $(NPM) run lint

test: test-backend test-frontend ## Run backend and frontend unit tests

test-backend: ## Run backend unit tests with locked dependencies
	cd backend && $(UV) run --locked --extra dev pytest -q

test-frontend: ## Run frontend unit tests
	cd frontend && $(NPM) run test

format: ## Format code (backend black/ruff)
	@echo "✨ Formatting backend code..."
	cd backend && $(UV) run --locked --extra dev black src tests
	cd backend && $(UV) run --locked --extra dev ruff check --fix src tests

build: ## Build the frontend production bundle
	@echo "🏗️ Building frontend..."
	cd frontend && $(NPM) run build

version: ## Synchronize the version number. Usage: make version VERSION=1.0.1
	@test -n "$(VERSION)" || (echo "VERSION is required, for example: make version VERSION=1.0.1" && exit 1)
	UV=$(UV) $(PYTHON) scripts/sync_version.py $(VERSION)

dbmate-migrate: ## Run dbmate migrations against the current releases.db
	@echo "🛫 Running dbmate migrations..."
	cd backend && dbmate --url "sqlite:$$(pwd)/data/releases.db" --migrations-dir dbmate/migrations migrate

clean: ## Clean build artifacts and caches
	@echo "🧹 Cleaning build artifacts and caches..."
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf frontend/dist
