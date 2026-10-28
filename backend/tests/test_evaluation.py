"""Tests for the Milestone 3 evaluation/benchmarking layer: metric
definitions, baselines on identical input, smoke tests, and report
assembly. No live model calls anywhere in this file.
"""
from __future__ import annotations

import pytest

from app.evaluation import baselines
from app.evaluation.calibration import CALIBRATION_STATUS, run_threshold_sweep
from app.evaluation.dataset_loader import DatasetUnavailableError, load_challenge_dataset, load_synthetic_fixture_dataset
from app.evaluation.ground_truth import TRUE_PAIRS
from app.evaluation.metrics import (
    alert_pair_precision_recall,
    incident_level_recall,
    notification_reduction_pct,
    verification_failure_rate_pct,
)
from app.evaluation.report import build_evaluation_report, render_text_summary
from app.evaluation.smoke import run_smoke_tests
from app.fixtures_pipeline import REPLAY_ALERTS


# --- metrics.py: documented zero-denominator and unit behavior ---


def test_pair_precision_recall_zero_denominators_return_none():
    m = alert_pair_precision_recall(set(), set())
    assert m.precision_pct is None
    assert m.recall_pct is None
    assert m.true_positives == 0


def test_pair_precision_recall_exact_match():
    truth = {frozenset({"a", "b"}), frozenset({"a", "c"})}
    predicted = {frozenset({"a", "b"}), frozenset({"a", "c"})}
    m = alert_pair_precision_recall(predicted, truth)
    assert m.precision_pct == 100.0
    assert m.recall_pct == 100.0
    assert m.false_positives == 0
    assert m.false_negatives == 0


def test_pair_precision_recall_partial_overlap():
    truth = {frozenset({"a", "b"}), frozenset({"a", "c"})}
    predicted = {frozenset({"a", "b"}), frozenset({"x", "y"})}  # 1 correct, 1 wrong, 1 missed
    m = alert_pair_precision_recall(predicted, truth)
    assert m.true_positives == 1
    assert m.false_positives == 1
    assert m.false_negatives == 1
    assert m.precision_pct == 50.0
    assert m.recall_pct == 50.0


def test_incident_recall_exact_vs_partial_match_not_credited():
    true_incidents = [{"a", "b", "c"}]
    # a superset prediction (false merge) must NOT count as a successful recall
    recall, details = incident_level_recall([{"a", "b", "c", "z"}], true_incidents)
    assert recall == 0.0
    assert details[0].match_kind == "partial_superset"

    recall_exact, details_exact = incident_level_recall([{"a", "b", "c"}], true_incidents)
    assert recall_exact == 100.0
    assert details_exact[0].match_kind == "exact"


def test_incident_recall_no_true_incidents_is_none():
    recall, details = incident_level_recall([{"a"}], [])
    assert recall is None
    assert details == []


def test_notification_reduction_zero_raw_alerts_is_none():
    assert notification_reduction_pct(0, 0) is None


def test_notification_reduction_documented_definition():
    assert notification_reduction_pct(100, 5) == 95.0


def test_verification_failure_rate_empty_is_none():
    assert verification_failure_rate_pct([]) is None


def test_verification_failure_rate_counts_rejected_only():
    assert verification_failure_rate_pct(["verified", "verified", "rejected", "pending"]) == 25.0


# --- baselines.py: identical input, four comparable results ---


def test_baseline_no_grouping_has_no_pairs_and_full_notification_count():
    raw = [dict(a) for a in REPLAY_ALERTS]
    result = baselines.baseline_no_grouping(raw)
    assert result.predicted_pairs == set()
    assert result.notification_count == len(raw)


def test_baseline_exact_dedup_merges_only_true_duplicates():
    raw = [dict(a) for a in REPLAY_ALERTS]
    result = baselines.baseline_exact_dedup(raw)
    # R-A1 and R-DUP1 share service+signal exactly; nothing else does
    merged_group = next(g for g in result.predicted_incidents if len(g) > 1)
    assert merged_group == {"R-A1", "R-DUP1"}
    assert result.notification_count == len(raw) - 1  # one pair collapsed


def test_baseline_deterministic_matches_ground_truth_on_replay():
    raw = [dict(a) for a in REPLAY_ALERTS]
    result = baselines.baseline_deterministic(raw)
    m = alert_pair_precision_recall(result.predicted_pairs, TRUE_PAIRS)
    assert m.precision_pct == 100.0
    assert m.recall_pct == 100.0
    assert m.false_positives == 0
    assert m.false_negatives == 0


def test_baseline_hybrid_does_not_change_grouping_vs_deterministic():
    """AI only assists Investigation, which runs after Triage has already
    decided group membership — hybrid's alert-pair metrics must therefore
    be identical to the deterministic baseline's, on this dataset.
    """
    raw = [dict(a) for a in REPLAY_ALERTS]
    deterministic = baselines.baseline_deterministic(raw)
    hybrid = baselines.baseline_hybrid(raw)
    assert hybrid.predicted_pairs == deterministic.predicted_pairs
    assert hybrid.notification_count == deterministic.notification_count
    # but it DID actually call the (mocked) model, for the ambiguous-band cases
    assert hybrid.ai_calls_made > 0


def test_all_four_baselines_use_identical_input_and_are_comparable():
    raw = [dict(a) for a in REPLAY_ALERTS]
    results = [
        baselines.baseline_no_grouping(raw),
        baselines.baseline_exact_dedup(raw),
        baselines.baseline_deterministic(raw),
        baselines.baseline_hybrid(raw),
    ]
    all_ids = {a["id"] for a in raw}
    for r in results:
        covered = {i for g in r.predicted_incidents for i in g}
        # every baseline's predicted groups only ever reference real input alert IDs
        assert covered.issubset(all_ids)


# --- calibration.py: threshold sweep is a real diagnostic, not a no-op ---


def test_calibration_status_is_not_calibrated():
    assert CALIBRATION_STATUS == "NOT_CALIBRATED"


def test_threshold_sweep_actually_changes_behavior():
    """Regression test for a real bug found while building this milestone:
    triage_agent.py originally imported GROUPING_THRESHOLD by value, so
    mutating correlation.GROUPING_THRESHOLD at runtime had no effect. This
    asserts the sweep genuinely moves the metrics, not just that it runs.
    """
    raw = [dict(a) for a in REPLAY_ALERTS]
    points = {p.threshold: p for p in run_threshold_sweep(raw, [0.2, 0.5, 0.8])}

    assert points[0.5].precision_pct == 100.0
    assert points[0.5].recall_pct == 100.0

    assert points[0.8].recall_pct == 0.0  # threshold too high: nothing groups
    assert points[0.2].false_positive_pairs > 0  # threshold too low: over-merges


# --- smoke.py: Mode A ---


def test_smoke_tests_all_pass():
    report = run_smoke_tests()
    failed = [c for c in report.checks if not c.passed]
    assert not failed, f"Synthetic smoke test failures: {failed}"
    assert report.provenance == "SYNTHETIC TEST RESULT"


# --- dataset_loader.py: Mode B honesty ---


def test_challenge_dataset_unavailable_is_explicit_not_fabricated():
    with pytest.raises(DatasetUnavailableError):
        load_challenge_dataset()


def test_synthetic_fixture_dataset_loads_with_fingerprint():
    info = load_synthetic_fixture_dataset()
    assert info.provenance == "synthetic_fixture"
    assert info.record_count > 0
    assert len(info.fingerprint) == 16


def test_fingerprint_is_stable_across_runs():
    a = load_synthetic_fixture_dataset()
    b = load_synthetic_fixture_dataset()
    assert a.fingerprint == b.fingerprint


# --- report.py: provenance labelling ---


def test_report_builds_and_labels_every_section():
    report = build_evaluation_report()
    assert report["mode_a_synthetic_smoke_tests"]["provenance"] == "SYNTHETIC TEST RESULT"
    assert report["mode_b_challenge_dataset_benchmark"]["provenance"] == "NOT MEASURED"
    assert report["mode_c_selective_ai_evaluation"]["provenance"] == "NOT MEASURED"
    assert report["baselines"]["provenance"] == "SYNTHETIC TEST RESULT"
    assert len(report["baselines"]["results"]) == 4
    # The forward-order replay itself has zero findings against its documented
    # expectations; the one standing finding is the known order-sensitivity
    # reliability gap (see error_analysis.analyze_order_sensitivity).
    assert report["error_analysis"]["total_findings"] == 1
    assert report["error_analysis"]["findings"][0]["category"] == "weak_correlation_rules"


def test_report_text_summary_renders():
    report = build_evaluation_report()
    text = render_text_summary(report)
    assert "Evaluation Report" in text
    assert "no_grouping" in text
