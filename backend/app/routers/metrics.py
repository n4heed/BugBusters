from __future__ import annotations

from fastapi import APIRouter

from .. import metrics as metrics_module
from ..dataset_registry import registry
from ..models import EvaluationMetrics
from ..store import Store

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("", response_model=EvaluationMetrics)
def get_metrics() -> EvaluationMetrics:
    store = registry.get_active().store or Store(seed_demo_fixtures=False)
    return metrics_module.compute_metrics(store)
