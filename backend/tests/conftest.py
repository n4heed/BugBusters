import os

import pytest

from app.orchestrator import Orchestrator
from app.store import Store

RUN_LIVE_LLM_TEST = os.environ.get("RUN_LIVE_LLM_TEST", "").strip().lower() in {"1", "true", "yes"}


@pytest.fixture(autouse=True, scope="session")
def _test_database_path(tmp_path_factory):
    """The dataset-persistence repository (app/persistence/db.py) resolves
    DATABASE_PATH fresh on every connection, never at import time — set
    once here for the whole test session so the suite never reads or
    writes a developer's real backend/data/bugbusters.sqlite3. Shared for
    the session (not per-test) to match the existing pattern of
    `dataset_registry.registry` itself being one shared singleton across
    the whole test run (see test_ingestion.py's isolation tests).
    """
    db_path = tmp_path_factory.mktemp("db") / "test_bugbusters.sqlite3"
    os.environ["DATABASE_PATH"] = str(db_path)


@pytest.fixture(autouse=True)
def _no_live_ai_by_default(monkeypatch):
    """A fresh `Orchestrator` loads AI config straight from the environment,
    including whatever is in a developer's local backend/.env. Routine test
    runs must never depend on, or spend credits against, real credentials —
    so every test is forced AI-disabled here unless it is the one opt-in
    live integration test (RUN_LIVE_LLM_TEST=1), which manages its own
    config and must see the real values.
    """
    if RUN_LIVE_LLM_TEST:
        return
    for var in ("LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL"):
        monkeypatch.setenv(var, "")
    monkeypatch.setenv("LLM_ENABLED", "false")


@pytest.fixture()
def fresh_store() -> Store:
    """A brand-new Store instance per test — no shared singleton state."""
    return Store()


@pytest.fixture()
def orchestrator(fresh_store: Store) -> Orchestrator:
    return Orchestrator(fresh_store)
