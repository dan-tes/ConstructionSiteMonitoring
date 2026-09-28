# СтройМонитор

Мониторинг хода строительства по видео и фото с объекта. Прораб ведёт по
проекту журнал: загружает план работ и записи с видео/фото. Модели
распознают технику на площадке, определяют текущую фазу строительства и
прогнозируют отставание от плана. В конце получается отчёт «план — факт».

Стек: FastAPI + PostgreSQL + RabbitMQ (backend), пять ML-сервисов на
Python (YOLO, DINOv2/SigLIP, классификатор фаз, прогноз по Earned Schedule,
YandexGPT), React + Vite (frontend).

## Требования

- Docker и Docker Compose
- Node.js и npm
- около 7 ГБ оперативной памяти на весь стек и около 11 ГБ на диске под
  Docker-образы
- GPU NVIDIA с CUDA для переоценки моделей (`cv/`,
  `phase_determination/`). Сам стек в Docker работает на CPU, для него GPU
  не нужен
- файлы весов на своих местах (в git их нет, скопировать вручную):
  - `services/vision/weights/best.pt`: детектор техники
  - `services/visual_phase/weights/backbone_best.pt`: визуальный энкодер
- ключ Yandex Cloud (`YANDEX_CLOUD_FOLDER`, `YANDEX_CLOUD_API_KEY`): без
  него сервис `planner` не запускается

## Запуск

```bash
cp backend/.env.example backend/.env   # заполнить YANDEX_CLOUD_FOLDER и YANDEX_CLOUD_API_KEY
(cd frontend && npm install)
scripts/dev_up.sh
```

- Frontend: http://localhost:5173
- API и Swagger: http://localhost:8000/docs
- RabbitMQ UI: http://localhost:15672 (guest/guest)

Остановить backend: `cd backend && docker compose down`.

## Документация

- [Итоговые результаты](docs/results.md): что сделано, ключевые метрики, выводы и что осталось
- Модули: зачем нужен каждый, как он работает и какое у него качество
  - [1. План объекта](docs/modules/planner.md)
  - [2. Детекция техники](docs/modules/vision.md)
  - [2б. Фаза по снимку](docs/modules/visual_phase.md)
  - [3. Фаза проекта](docs/modules/phase.md)
  - [4. Прогноз отставания](docs/modules/delay.md)
  - [5–6. Текстовые отчёты](docs/modules/reports.md)
- [Тесты](docs/testing.md): что покрыто и как запускать
- [Разработка](docs/development.md): запуск по частям, переменные окружения, миграции
- [Архитектура](docs/architecture.md): компоненты и путь видео через конвейер анализа
- [API](docs/api.md): авторизация, эндпоинты, хранение файлов
- [ML-исследования](docs/ml.md): что лежит в `cv/`, `phase_determination/`, `latency_prediction/`
