from __future__ import annotations

from fastapi import APIRouter

from ..dataset_registry import registry
from ..models import SeaMemoryEvent

router = APIRouter(prefix="/api/sea-memory", tags=["sea-memory"])


@router.get("", response_model=list[SeaMemoryEvent])
def list_memory_events() -> list[SeaMemoryEvent]:
    store = registry.get_active().store
    if store is None:
        return []
    return sorted(store.memory_log, key=lambda e: e.timestamp)
