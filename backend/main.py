import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from analysis import requeue_pending, start_consumers
from config import settings
from integrations import broker
from routers import auth, files, projects

log = logging.getLogger("csm")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.media_root.mkdir(parents=True, exist_ok=True)
    await start_consumers()
    try:
        await requeue_pending()
    except Exception:  # pragma: no cover - best effort on boot
        log.exception("failed to requeue pending video analyses")
    yield
    await broker.close()


app = FastAPI(title="Construction Site Monitoring API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(files.router)


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
