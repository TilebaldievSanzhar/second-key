# Частые команды. На Windows — через Git Bash (make из Chocolatey/Scoop) или см. README.
COMPOSE      = docker compose
COMPOSE_PROD = docker compose -f compose.yaml -f compose.prod.yaml
BACKEND      = cd backend &&

.PHONY: help env up down logs ps seed migrate revision test lint fmt bench prod-up prod-down frontend-dev api-types

help:            ## список команд
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

env:             ## создать .env из примера со случайными секретами
	@test -f .env && echo ".env уже есть" || python scripts/make_env.py

up:              ## dev-стек с hot reload (UI :5173, API :8000)
	$(COMPOSE) up -d --build

down:            ## остановить стек (данные в томах сохраняются)
	$(COMPOSE) down

logs:            ## логи api и worker
	$(COMPOSE) logs -f api worker

ps:
	$(COMPOSE) ps

seed:            ## демо-организация, пользователи и демо-аудит
	$(COMPOSE) run --rm api secondkey seed-demo

migrate:         ## применить миграции
	$(COMPOSE) run --rm migrate

revision:        ## новая миграция по моделям: make revision m="что изменилось"
	$(COMPOSE) run --rm migrate alembic revision --autogenerate -m "$(m)"

test:            ## тесты бэкенда (SQLite, без внешних сервисов)
	$(BACKEND) uv run pytest -q

lint:            ## ruff + проверка типов фронта
	$(BACKEND) uv run ruff check . && uv run ruff format --check .
	cd frontend && npm run lint

fmt:
	$(BACKEND) uv run ruff format . && uv run ruff check --fix .

bench:           ## бенчмарк движка на синтетике (качество для CI)
	$(BACKEND) uv run secondkey bench

api-types:       ## TS-типы фронта из OpenAPI бэкенда (без запуска сервера)
	$(BACKEND) uv run secondkey openapi --out ../frontend/openapi.json
	cd frontend && npm run api-types

prod-up:         ## прод-режим на одном хосте (nginx :8080)
	$(COMPOSE_PROD) up -d --build

prod-down:
	$(COMPOSE_PROD) down
