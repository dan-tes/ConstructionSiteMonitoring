# СтройМонитор: backend

API на FastAPI + PostgreSQL. Отвечает за авторизацию, проекты, журнал
видео и фото и оркестрацию конвейера анализа через RabbitMQ.

```bash
cp .env.example .env
docker compose up -d --build   # API: http://localhost:8000/docs
uv run pytest                  # тесты (нужен uv sync --group dev)
```

Подробнее:
- [Разработка](../docs/development.md)
- [API](../docs/api.md)
- [Архитектура](../docs/architecture.md)
- [Конвейер анализа](integrations/README.md)
