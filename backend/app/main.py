from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .dataset_registry import registry
from .orchestrator import orchestrator
from .routers import datasets, evaluation, incidents, metrics, pipeline, sea_memory, stream, tuning

logger = logging.getLogger("bugbusters.startup")

app = FastAPI(title="Bug Busters AIOps Command Center API")


@app.on_event("startup")
def log_ai_configuration() -> None:
    """Shows whether AI integration is configured, without ever revealing
    the credential — see app/ai/config.py AIConfig.summary().
    """
    summary = orchestrator.ai_config.summary()
    if summary["ai_enabled"]:
        logger.info("AI integration ENABLED: %s", summary)
    else:
        logger.info("AI integration DISABLED — deterministic pipeline only: %s", summary)


@app.on_event("startup")
def restore_persisted_datasets() -> None:
    """Restores previously-imported datasets (and the active-dataset
    selection) from SQLite — see dataset_registry.DatasetRegistry.
    hydrate_from_db. Runs after the module-level `registry` singleton has
    already registered the in-memory "default" demo dataset, which this
    never touches.
    """
    before = len(registry.datasets)
    registry.hydrate_from_db()
    restored = len(registry.datasets) - before
    logger.info("Dataset persistence: restored %d dataset(s) from SQLite (active=%s)", restored, registry.active_dataset_id)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    # Vite picks a different port when 5173 is already taken (as it was
    # during this milestone's own verification) — allow any localhost port
    # in addition to the pinned default, for local dev only.
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(incidents.router)
app.include_router(tuning.router)
app.include_router(sea_memory.router)
app.include_router(metrics.router)
app.include_router(stream.router)
app.include_router(pipeline.router)
app.include_router(evaluation.router)
app.include_router(datasets.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
