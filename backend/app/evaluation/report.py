"""Assembles the machine-readable evaluation report and a concise
human-readable summary. Every section carries explicit provenance
(`SYNTHETIC TEST RESULT` / `CHALLENGE DATASET RESULT` / `LIVE MODEL SAMPLE`
/ `NOT MEASURED`) so nothing here can be mistaken for a real benchmark.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import baselines
from .dataset_loader import DatasetUnavailableError, load_challenge_dataset, load_synthetic_fixture_dataset
from .error_analysis import (
    analyze_against_expected_status,
    analyze_ai_call_outcomes,
    analyze_order_sensitivity,
    analyze_processing_errors,
)
from .ground_truth import REPLAY_ALERT_IDS, TRUE_PAIRS
from .metrics import alert_pair_precision_recall
from .replay import run_deterministic_replay
from .smoke import run_smoke_tests
from ..fixtures_pipeline import EXPECTED_ALERT_WORKFLOW_STATUS, REPLAY_ALERTS


def _baseline_summary(result) -> dict[str, Any]:
    pair_metrics = alert_pair_precision_recall(result.predicted_pairs, TRUE_PAIRS)
    return {
        "name": result.name,
        "notification_count": result.notification_count,
        "precision_pct": pair_metrics.precision_pct,
        "recall_pct": pair_metrics.recall_pct,
        "true_positives": pair_metrics.true_positives,
        "false_positives": pair_metrics.false_positives,
        "false_negatives": pair_metrics.false_negatives,
        "ai_calls_made": result.ai_calls_made,
        "verification_failures": sum(1 for o in result.verification_outcomes if o == "rejected"),
        "total_latency_ms": result.total_latency_ms,
        "notes": result.notes,
    }


def build_evaluation_report() -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).isoformat()

    # Mode A — synthetic smoke tests
    smoke = run_smoke_tests()
    smoke_section = {
        "provenance": smoke.provenance,
        "total_checks": len(smoke.checks),
        "passed": smoke.pass_count,
        "all_passed": smoke.all_passed,
        "checks": [
            {"name": c.name, "category": c.category, "passed": c.passed, "detail": c.detail} for c in smoke.checks
        ],
    }

    # Mode B — ground-truth benchmark against the real challenge dataset
    try:
        challenge = load_challenge_dataset()
        mode_b_section = {"provenance": "CHALLENGE DATASET RESULT", "dataset": challenge.__dict__}
    except DatasetUnavailableError as exc:
        mode_b_section = {
            "provenance": "NOT MEASURED",
            "reason": str(exc),
        }

    # Baselines 1-4 on identical input
    raw_alerts = [dict(a) for a in REPLAY_ALERTS]
    b1 = baselines.baseline_no_grouping(raw_alerts)
    b2 = baselines.baseline_exact_dedup(raw_alerts)
    b3 = baselines.baseline_deterministic(raw_alerts)
    b4 = baselines.baseline_hybrid(raw_alerts)
    baselines_section = {
        "provenance": "SYNTHETIC TEST RESULT",
        "dataset": "fixtures_pipeline.REPLAY_ALERTS (6 alerts, explicit ground truth)",
        "results": [_baseline_summary(b) for b in (b1, b2, b3, b4)],
    }

    # Error analysis against the deterministic replay (baseline 3's own run,
    # re-run fresh here so the error analysis is self-contained)
    error_store, _ = run_deterministic_replay()
    errors = (
        analyze_against_expected_status(error_store, EXPECTED_ALERT_WORKFLOW_STATUS)
        + analyze_processing_errors(error_store)
        + analyze_ai_call_outcomes(error_store)
        + analyze_order_sensitivity()
    )
    error_section = {
        "provenance": "SYNTHETIC TEST RESULT",
        "total_findings": len(errors),
        "findings": [
            {
                "case_ref": e.case_ref,
                "stage": e.stage,
                "category": e.category,
                "observed": e.observed,
                "expected": e.expected,
                "alert_ids": e.alert_ids,
            }
            for e in errors
        ],
    }

    # Mode C — selective AI evaluation: routine comparisons already covered
    # by baseline 4 (mocked provider). No live-model sample has been
    # explicitly authorized/run for this report.
    mode_c_section = {
        "provenance": "NOT MEASURED",
        "reason": (
            "No live model provider credentials are configured in this environment, and no explicit "
            "opt-in live evaluation run has been performed. Mocked-provider comparisons are reported "
            "under 'baselines' (selective_hybrid) instead — those are NOT a measurement of real model quality."
        ),
        "representative_case_ids": REPLAY_ALERT_IDS,
    }

    fixture_dataset = load_synthetic_fixture_dataset()

    return {
        "generated_at": generated_at,
        "dataset_mode_a": fixture_dataset.__dict__,
        "mode_a_synthetic_smoke_tests": smoke_section,
        "mode_b_challenge_dataset_benchmark": mode_b_section,
        "mode_c_selective_ai_evaluation": mode_c_section,
        "baselines": baselines_section,
        "error_analysis": error_section,
    }


def render_text_summary(report: dict[str, Any]) -> str:
    lines = [
        f"Bug Busters AIOps — Evaluation Report ({report['generated_at']})",
        "",
        f"[Mode A] Synthetic smoke tests: {report['mode_a_synthetic_smoke_tests']['passed']}/"
        f"{report['mode_a_synthetic_smoke_tests']['total_checks']} passed "
        f"({report['mode_a_synthetic_smoke_tests']['provenance']})",
        f"[Mode B] Challenge dataset benchmark: {report['mode_b_challenge_dataset_benchmark']['provenance']}",
        f"[Mode C] Selective AI evaluation: {report['mode_c_selective_ai_evaluation']['provenance']}",
        "",
        "Baselines (identical 6-alert replay input, SYNTHETIC TEST RESULT):",
    ]
    for b in report["baselines"]["results"]:
        lines.append(
            f"  - {b['name']}: notifications={b['notification_count']} "
            f"precision={b['precision_pct']} recall={b['recall_pct']} "
            f"ai_calls={b['ai_calls_made']} latency_ms={b['total_latency_ms']}"
        )
    lines.append("")
    lines.append(f"Error analysis findings: {report['error_analysis']['total_findings']}")
    for f in report["error_analysis"]["findings"]:
        lines.append(f"  - [{f['case_ref']}] {f['category']}: observed={f['observed']!r} expected={f['expected']!r}")
    return "\n".join(lines)
