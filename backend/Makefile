PYTHON  ?= python3.13
VENV    := .venv
BIN     := $(VENV)/bin

.DEFAULT_GOAL := help

.PHONY: help
help: ## Muestra esta ayuda
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

$(BIN)/python:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip

.PHONY: install
install: $(BIN)/python ## Crea el venv e instala dependencias
	$(BIN)/pip install -e ".[dev]"

.PHONY: up
up: ## Levanta Postgres y Mailhog
	cd .. && docker compose up -d postgres postgres_test mailhog
	@echo "esperando a Postgres..."
	@until docker exec crm-postgres pg_isready -U crm -d crm >/dev/null 2>&1; do sleep 1; done
	@until docker exec crm-postgres-test pg_isready -U crm -d crm_test >/dev/null 2>&1; do sleep 1; done
	@echo "listo. Mailhog UI: http://localhost:8025"

.PHONY: down
down: ## Para los contenedores
	cd .. && docker compose down

.PHONY: reset-db
reset-db: ## BORRA la base de desarrollo y la recrea desde cero
	cd .. && docker compose down -v postgres
	$(MAKE) up
	$(MAKE) migrate

.PHONY: migrate
migrate: ## Aplica las migraciones pendientes
	$(BIN)/alembic upgrade head

.PHONY: downgrade
downgrade: ## Revierte la última migración
	$(BIN)/alembic downgrade -1

.PHONY: revision
revision: ## Genera migración: make revision m="descripcion"
	@test -n "$(m)" || (echo "uso: make revision m=\"descripcion\"" && exit 1)
	$(BIN)/alembic revision --autogenerate -m "$(m)"

.PHONY: dev
dev: ## Arranca la API con recarga automática
	$(BIN)/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

.PHONY: test
test: ## Ejecuta los tests
	$(BIN)/pytest -q

.PHONY: lint
lint: ## Lint + comprobación de formato
	$(BIN)/ruff check app tests migrations
	$(BIN)/ruff format --check app tests migrations

.PHONY: fmt
fmt: ## Formatea y arregla lo autoarreglable
	$(BIN)/ruff format app tests migrations
	$(BIN)/ruff check --fix app tests migrations

.PHONY: typecheck
typecheck: ## Comprobación de tipos
	$(BIN)/mypy app

.PHONY: check
check: lint typecheck test ## Todo lo que corre en CI
