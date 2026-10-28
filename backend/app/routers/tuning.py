from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..dataset_registry import registry
from ..models import ApprovalResult, TuningRecommendation

router = APIRouter(prefix="/api/tuning", tags=["tuning"])


@router.get("", response_model=list[TuningRecommendation])
def list_recommendations() -> list[TuningRecommendation]:
    store = registry.get_active().store
    return list(store.tuning.values()) if store is not None else []


@router.post("/{rule_id}/approve", response_model=ApprovalResult)
def approve_recommendation(rule_id: str) -> ApprovalResult:
    store = registry.get_active().store
    if store is None:
        raise HTTPException(status_code=400, detail="The active dataset has not been loaded yet.")
    result = store.approve_tuning(rule_id)
    if not result.success:
        raise HTTPException(status_code=404, detail=result.message)
    return result


@router.post("/{rule_id}/reject", response_model=ApprovalResult)
def reject_recommendation(rule_id: str) -> ApprovalResult:
    store = registry.get_active().store
    if store is None:
        raise HTTPException(status_code=400, detail="The active dataset has not been loaded yet.")
    result = store.reject_tuning(rule_id)
    if not result.success:
        raise HTTPException(status_code=404, detail=result.message)
    return result
