from __future__ import annotations

from fastapi import APIRouter

from ..evaluation.report import build_evaluation_report, render_text_summary

router = APIRouter(prefix="/api/evaluation", tags=["evaluation"])


@router.get("/report")
def get_evaluation_report() -> dict:
    """Full machine-readable evaluation report: Mode A synthetic smoke
    tests, Mode B/C provenance-labelled status, the 4 baselines run on
    identical input, and per-case error analysis. Every section is
    computed fresh on each call (cheap: 6-alert replay + a handful of
    isolated smoke checks, no model calls, no dataset-wide processing).
    """
    return build_evaluation_report()


@router.get("/report/summary")
def get_evaluation_summary() -> dict:
    report = build_evaluation_report()
    return {"text": render_text_summary(report)}
