# Разработка

## Структура репозитория

| Папка | Что внутри |
| --- | --- |
| `backend/` | FastAPI API, модели БД, миграции Alembic, оркестрация конвейера анализа |
| `services/` | ML-воркеры `planner`, `vision`, `visual_phase`, `phase`, `delay`. Каждый слушает свою очередь RabbitMQ |
| `frontend/` | SPA на React 19 + Vite + Tailwind |
| `scripts/` | `dev_up.sh` поднимает весь стек, `smoke_test_narrative_report.sh` прогоняет сквозной тест |
| `cv/`, `phase_determination/`, `latency_prediction/` | ноутбуки и скрипты обучения моделей, см. [ml.md](ml.md) |

## Весь стек одной командой

```bash
scripts/dev_up.sh [frontend_port]   # порт по умолчанию 5173
```

Скрипт запускает `docker compose up -d --build` в `backend/` (Postgres,
RabbitMQ, API и все пять сервисов), затем dev-сервер Vite. После этого он
показывает логи контейнеров. По Ctrl+C останавливается только frontend,
контейнеры backend продолжают работать.

## Запуск по частям

### Backend целиком в Docker

```bash
cd backend
cp .env.example .env
docker compose up -d --build
```

При старте контейнер `backend` выполняет `alembic upgrade head` и запускает
uvicorn с `--reload`. Исходники смонтированы в контейнер, так что правки
подхватываются без пересборки.

Сервис `planner` не стартует без `YANDEX_CLOUD_FOLDER` и
`YANDEX_CLOUD_API_KEY`. Compose берёт их из `backend/.env` или из окружения.

### API на хосте, остальное в Docker

```bash
cd backend
docker compose up -d db rabbitmq   # плюс нужные сервисы из services/
uv sync
uv run alembic upgrade head
uv run uvicorn main:app --reload
```

### Frontend

```bash
cd frontend
cp .env.example .env   # VITE_API_URL, по умолчанию http://localhost:8000
npm install
npm run dev
```

Остальные команды: `npm run build`, `npm run lint` (oxlint), `npm run preview`.

## Переменные окружения backend

Полный список с комментариями лежит в [`backend/.env.example`](../backend/.env.example).
Главные из них:

| Переменная | Назначение |
| --- | --- |
| `DATABASE_URL` | строка подключения SQLAlchemy, драйвер обязательно `asyncpg` |
| `SECRET_KEY` | секрет для детерминированных солей в `/auth/salt` |
| `SESSION_TTL_DAYS` | время жизни сессии, по умолчанию 7 дней |
| `CORS_ORIGINS`, `CORS_ORIGIN_REGEX` | разрешённые origin для браузера |
| `MEDIA_ROOT` | папка для загруженных файлов |
| `PUBLIC_BASE_URL` | адрес, по которому браузер получает файлы (`/files/{id}`) |
| `INTERNAL_BASE_URL` | адрес, по которому сервисы скачивают файлы изнутри сети compose |
| `MAX_UPLOAD_MB` | лимит размера одного файла |
| `RABBITMQ_URL` | брокер конвейера анализа |
| `YANDEX_CLOUD_FOLDER`, `YANDEX_CLOUD_API_KEY`, `YANDEX_CLOUD_MODEL` | YandexGPT. Для `planner` обязательны. Для текстовых отчётов в backend необязательны: без ключа отчёты просто не генерируются |

## Тесты

Подробно о том, что покрыто тестами, см. [testing.md](testing.md).

```bash
cd backend
uv sync --group dev
uv run pytest            # SQLite в памяти, Postgres не нужен
```

У сервисов тесты лежат рядом с кодом (`services/phase/test_fusion.py`,
`services/delay/test_worker.py`). Запускаются `pytest` из папки сервиса,
если установлены зависимости из её `requirements.txt`.

Сквозной тест на поднятом стеке регистрирует пользователя, создаёт проект,
загружает запись и ждёт статуса `ready`:

```bash
scripts/smoke_test_narrative_report.sh [media_file] [base_url]
```

## Миграции

```bash
cd backend
uv run alembic revision --autogenerate -m "описание изменения"
uv run alembic upgrade head
```

Alembic и приложение берут `DATABASE_URL` из одного места, `config.py`.
