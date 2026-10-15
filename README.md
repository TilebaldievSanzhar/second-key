# Второй ключ (Second Key)

Независимая проверка цены каждой закупки стройматериалов для собственника строительной
компании. Снабжение покупает, система проверяет, собственник решает.

- Продукт: [docs/product/STRATEGY.md](docs/product/STRATEGY.md), план: [docs/product/PLAN.md](docs/product/PLAN.md)
- Архитектура: [docs/architecture.md](docs/architecture.md), решения: [docs/adr/](docs/adr/)
- Интерфейс: ТЗ [docs/design/FRONTEND_SPEC.md](docs/design/FRONTEND_SPEC.md), макет и отличия реализации — [docs/design/README.md](docs/design/README.md)

## Состав

| Папка | Что |
|---|---|
| `backend/` | Python 3.13, FastAPI, SQLAlchemy 2 (async), Alembic, taskiq (Redis), движок аудита `secondkey.engine` |
| `frontend/` | React 19 + TypeScript + Vite, TanStack Query; в проде — статика за nginx |
| `compose.yaml` | Postgres 17, Redis 7, RustFS (S3), migrate, api, worker, scheduler, web |
| `docs/` | стратегия, план, ADR, архитектура, ТЗ на дизайн |

## Быстрый старт

```powershell
python scripts/make_env.py             # .env из .env.example со случайными секретами (или make env)
docker compose up -d --build           # весь стек с hot reload (compose.override.yaml)
docker compose run --rm api secondkey seed-demo   # демо-компания, пользователи, эталон рынка, аудит
```

| Что | Адрес |
|---|---|
| Интерфейс (Vite, hot reload) | http://localhost:5173 |
| API и Swagger | http://localhost:8000/api/docs |
| Консоль S3 (RustFS) | http://localhost:9001 — ключи `S3_ACCESS_KEY` / `S3_SECRET_KEY` из `.env` |

Демо-пользователи (пароль `demo-demo-2026`): `owner@demo.kg` — собственник, `finance@demo.kg`,
`accountant@demo.kg` — загружает выгрузки, `asanov@demo.kg` и `toktosunov@demo.kg` — снабжение
(видят только вопросы по своим закупкам).

Продовый режим без hot reload, наружу только nginx:

```powershell
docker compose -f compose.yaml -f compose.prod.yaml up -d --build   # http://localhost:8080
```

Обслуживание: `docker compose run --rm api secondkey --help` — `create-org`, `add-member`,
`collect-market` (цены госзакупок КР), `fetch-fx` (курсы НБКР), `bench` (качество движка), `openapi`.

## Разработка без Docker

```powershell
cd backend
uv sync                                # venv + зависимости по uv.lock
uv run pytest                          # тесты, включая сквозной сценарий API (SQLite, без внешних сервисов)
uv run secondkey bench                 # бенчмарк движка на синтетике; код 1 — качество ниже порога
uv run ruff check . ; uv run ruff format --check .
cd ..\frontend
npm ci ; npm run dev                   # http://localhost:5173, /api проксируется на :8000
```

Частые команды собраны в `Makefile` (`make up`, `make seed`, `make test`, `make lint`, `make revision m=…`,
`make api-types`). Новая миграция: `make revision m="что изменилось"`; типы фронта после изменения API:
`make api-types` (CI проверяет, что схема и типы закоммичены).

## Происхождение кода

Движок аудита (`backend/src/secondkey/engine/`) перенесён из исследовательского стенда
`procdesk` (челлендж MOST BI × BI Group), бенчмарк v11 от 14.10.2026. Развитие ядра идёт здесь;
procdesk заморожен как стенд казахстанского трека.
