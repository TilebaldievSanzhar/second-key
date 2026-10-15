# Второй ключ (Second Key)

Независимая проверка цены каждой закупки стройматериалов для собственника строительной
компании. Снабжение покупает, система проверяет, собственник решает.

- Продукт: [docs/product/STRATEGY.md](docs/product/STRATEGY.md), план: [docs/product/PLAN.md](docs/product/PLAN.md)
- Архитектура: [docs/architecture.md](docs/architecture.md), решения: [docs/adr/](docs/adr/)
- ТЗ на интерфейс для Claude Design: [docs/design/FRONTEND_SPEC.md](docs/design/FRONTEND_SPEC.md)

## Состав

| Папка | Что |
|---|---|
| `backend/` | Python 3.13, FastAPI, SQLAlchemy 2 (async), Alembic, taskiq (Redis), движок аудита `secondkey.engine` |
| `frontend/` | React 19 + TypeScript + Vite, TanStack Query; в проде — статика за nginx |
| `compose.yaml` | Postgres 17, Redis 7, MinIO (S3), api, worker, scheduler, migrate, web |
| `docs/` | стратегия, план, ADR, архитектура, ТЗ на дизайн |

## Быстрый старт

```powershell
Copy-Item .env.example .env            # заполнить секреты
docker compose up -d --build           # весь стек с hot reload (compose.override.yaml)
docker compose run --rm api secondkey seed-demo   # организация, владелец и демо-аудит
# интерфейс: http://localhost:5173   API: http://localhost:8000/docs   MinIO: http://localhost:9001
```

Продовый режим без hot reload и с nginx перед фронтом:

```powershell
docker compose -f compose.yaml -f compose.prod.yaml up -d --build   # http://localhost:8080
```

## Разработка без Docker

```powershell
cd backend
uv sync                                # venv + зависимости по uv.lock
uv run pytest                          # тесты (SQLite, без внешних сервисов)
uv run ruff check . ; uv run ruff format --check .
cd ..\frontend
npm ci ; npm run dev                   # http://localhost:5173, /api проксируется на :8000
```

Частые команды собраны в `Makefile` (`make up`, `make test`, `make lint`, `make migrate`).

## Происхождение кода

Движок аудита (`backend/src/secondkey/engine/`) перенесён из исследовательского стенда
`procdesk` (челлендж MOST BI × BI Group), бенчмарк v11 от 14.10.2026. Развитие ядра идёт здесь;
procdesk заморожен как стенд казахстанского трека.
