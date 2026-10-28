from __future__ import annotations

from itertools import combinations
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ..dataset_registry import DatasetNotFoundError, registry
from ..evaluation.metrics import alert_pair_precision_recall
from ..ingestion.base import UnsupportedFormatError
from ..ingestion.import_service import (
    ImportError_,
    confirm_mapping,
    expand_uploaded_files,
    import_dataset,
    load_dataset,
    run_replay,
    start_processing_run,
    validate_dataset,
)
from ..ingestion.zip_bundle_adapter import UnsafeZipError
from ..persistence import repository
from ..schemas.agent_results import ProcessingResult
from ..schemas.dataset import DatasetSummary, FieldMappingEntry

router = APIRouter(prefix="/api/datasets", tags=["datasets"])


def _not_found(dataset_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Dataset {dataset_id!r} not found.")


@router.post("/import", response_model=DatasetSummary)
async def import_dataset_endpoint(
    name: str = Form(...),
    files: list[UploadFile] = File(...),
) -> DatasetSummary:
    """Stage A/B/C: upload one or more files (or a single .zip bundle) and
    get back detected schema + suggested (not confirmed) category/field
    mappings per file. No records are validated or normalized yet.
    """
    uploads = [(f.filename or "unnamed", await f.read()) for f in files]
    try:
        expanded = expand_uploaded_files(uploads)
        record = import_dataset(name, expanded)
    except (UnsupportedFormatError, UnsafeZipError, ImportError_) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return record.summary(registry.active_dataset_id)


@router.get("", response_model=list[DatasetSummary])
def list_datasets() -> list[DatasetSummary]:
    return registry.list_summaries()


@router.get("/{dataset_id}", response_model=DatasetSummary)
def get_dataset(dataset_id: str) -> DatasetSummary:
    try:
        record = registry.get(dataset_id)
    except DatasetNotFoundError:
        raise _not_found(dataset_id)
    return record.summary(registry.active_dataset_id)


@router.get("/{dataset_id}/schema")
def get_dataset_schema(dataset_id: str) -> dict:
    try:
        record = registry.get(dataset_id)
    except DatasetNotFoundError:
        raise _not_found(dataset_id)
    return {
        "files": {name: info.model_dump(mode="json") for name, info in record.files.items()},
        "suggested_mappings": {name: m.model_dump(mode="json") for name, m in record.file_mappings.items()},
    }


class ConfirmMappingRequest(BaseModel):
    file_name: str
    category: str
    field_mappings: list[FieldMappingEntry]


@router.post("/{dataset_id}/mapping", response_model=DatasetSummary)
def confirm_mapping_endpoint(dataset_id: str, payload: ConfirmMappingRequest) -> DatasetSummary:
    try:
        record = confirm_mapping(dataset_id, payload.file_name, payload.category, payload.field_mappings)
    except DatasetNotFoundError:
        raise _not_found(dataset_id)
    except ImportError_ as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return record.summary(registry.active_dataset_id)


@router.post("/{dataset_id}/validate", response_model=DatasetSummary)
def validate_dataset_endpoint(dataset_id: str) -> DatasetSummary:
    try:
        record = validate_dataset(dataset_id)
    except DatasetNotFoundError:
        raise _not_found(dataset_id)
    except ImportError_ as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return record.summary(registry.active_dataset_id)


@router.post("/{dataset_id}/load", response_model=DatasetSummary)
def load_dataset_endpoint(dataset_id: str) -> DatasetSummary:
    try:
        record = load_dataset(dataset_id)
    except DatasetNotFoundError:
        raise _not_found(dataset_id)
    except ImportError_ as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return record.summary(registry.active_dataset_id)


@router.post("/{dataset_id}/select", response_model=DatasetSummary)
def select_dataset_endpoint(dataset_id: str) -> DatasetSummary:
    try:
        registry.set_active(dataset_id)
        record = registry.get(dataset_id)
    except DatasetNotFoundError:
        raise _not_found(dataset_id)
    return record.summary(registry.active_dataset_id)


class ReplayRunSummary(BaseModel):
    run_id: str
    dataset_id: str
    alert_count: int
    workflow_status_counts: dict[str, int]
    # Additive fields (Phase 2) — never removes/renames the originals above,
    # so existing callers of this response model are unaffected.
    status: str = "completed"
    duplicate_count: int = 0
    group_count: int = 0
    incident_count: int = 0
    # Additive fields (Phase 3) — see RunSummary below for semantics.
    unique_count: int = 0
    uncertain_count: int = 0
    grouped_alert_count: int = 0
    standalone_count: int = 0


class RunSummary(BaseModel):
    run_id: str
    dataset_id: str
    started_at: str
    completed_at: Optional[str] = None
    status: str
    eligible_alert_count: int
    processed_count: int
    workflow_status_counts: dict[str, int]
    duplicate_count: int
    group_count: int
    incident_count: int
    error_summary: Optional[str] = None
    # Phase 3 additions. unique_count + duplicate_count + uncertain_count ==
    # processed_count. grouped_alert_count + standalone_count == unique_count.
    # group_count counts distinct valid (>=2 member) incident groups and is
    # NOT assumed equal to incident_count (a richer, separately-tracked
    # investigated/verified record — see models.Incident).
    unique_count: int = 0
    uncertain_count: int = 0
    grouped_alert_count: int = 0
    standalone_count: int = 0
    # Phase 4 addition — see RunStatsSummary for the fuller live-stats shape.
    last_progress_at: Optional[str] = None


class StartRunResponse(BaseModel):
    """Response for POST /{dataset_id}/runs (Phase 4's async start). The
    run row is already durably persisted (status='running') by the time
    this returns — a client can immediately begin polling the summary
    endpoint with this run_id.
    """

    run_id: str
    dataset_id: str
    status: str
    eligible_alert_count: int
    started_at: str
    already_running: bool = False  # True when this returned an EXISTING active run instead of starting a new one


class RunStatsSummary(BaseModel):
    """GET /{dataset_id}/runs/{run_id}/summary — restart-safe, works
    before/during/after processing. All counts come from
    repository.compute_run_statistics, the same shared aggregation
    `complete_run`'s final persisted counts are derived from.
    """

    dataset_id: str
    run_id: str
    status: str
    eligible_alert_count: int
    processed_count: int
    unique_count: int
    duplicate_count: int
    uncertain_count: int
    group_count: int
    grouped_alert_count: int
    unique_grouped_alert_count: int
    standalone_count: int
    incident_count: int
    workflow_status_counts: dict[str, int]
    started_at: str
    completed_at: Optional[str] = None
    last_progress_at: Optional[str] = None
    error_summary: Optional[str] = None


class GroupMember(BaseModel):
    alert_id: str
    service: Optional[str] = None
    severity: Optional[str] = None
    timestamp: Optional[str] = None
    workflow_status: Optional[str] = None
    disposition: Optional[str] = None
    duplicate_of: Optional[str] = None


class GroupDetail(BaseModel):
    group_id: str
    dataset_id: str
    run_id: str
    size: int
    unique_member_count: int
    redundant_member_count: int
    members: list[GroupMember]
    incident: Optional[dict] = None


@router.post("/{dataset_id}/replay", response_model=ReplayRunSummary)
def replay_dataset_endpoint(dataset_id: str) -> ReplayRunSummary:
    """Processes THIS dataset's own persisted, validated canonical alerts —
    never the global synthetic-demo fixture used by `POST /api/pipeline/
    replay`, which is a completely separate endpoint. See
    ingestion.import_service.run_replay for the full processing contract.
    """
    try:
        run = run_replay(dataset_id)
    except DatasetNotFoundError:
        raise _not_found(dataset_id)
    except ImportError_ as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    summary = repository.load_run_summary(run.id, dataset_id)
    counts: dict[str, int] = {}
    for r in run.results:
        key = r.workflow_status.value if hasattr(r.workflow_status, "value") else str(r.workflow_status)
        counts[key] = counts.get(key, 0) + 1
    return ReplayRunSummary(
        run_id=run.id,
        dataset_id=dataset_id,
        alert_count=len(run.results),
        workflow_status_counts=counts,
        status=summary["status"] if summary else "completed",
        duplicate_count=summary["duplicate_count"] if summary else 0,
        group_count=summary["group_count"] if summary else 0,
        incident_count=summary["incident_count"] if summary else 0,
        unique_count=summary["unique_count"] if summary else 0,
        uncertain_count=summary["uncertain_count"] if summary else 0,
        grouped_alert_count=summary["grouped_alert_count"] if summary else 0,
        standalone_count=summary["standalone_count"] if summary else 0,
    )


@router.get("/{dataset_id}/runs", response_model=list[RunSummary])
def list_runs_endpoint(dataset_id: str) -> list[RunSummary]:
    try:
        registry.get(dataset_id)  # 404s for an unknown dataset, same as every other dataset-scoped route
    except DatasetNotFoundError:
        raise _not_found(dataset_id)
    return [RunSummary(**s) for s in repository.list_run_summaries(dataset_id)]


@router.post("/{dataset_id}/runs", response_model=StartRunResponse)
def start_run_endpoint(dataset_id: str) -> StartRunResponse:
    """Phase 4: starts processing in the background and returns immediately
    with the run's ID — the frontend then polls GET .../summary rather
    than waiting on this request. The OLD synchronous `POST .../replay`
    endpoint above is unchanged and still blocks until completion; this is
    an ADDITIVE alternative, not a replacement.

    Rapidly clicking "start processing" is handled here, not by erroring:
    if this dataset already has an active run, that run's own info is
    returned (already_running=True) instead of attempting to start a
    second worker that would race the first one over the same dataset's
    in-memory Store/Orchestrator.
    """
    try:
        registry.get(dataset_id)
    except DatasetNotFoundError:
        raise _not_found(dataset_id)

    existing_run_id = repository.has_active_run(dataset_id)
    if existing_run_id is not None:
        summary = repository.load_run_summary(existing_run_id, dataset_id)
        if summary is not None:
            return StartRunResponse(
                run_id=existing_run_id,
                dataset_id=dataset_id,
                status=summary["status"],
                eligible_alert_count=summary["eligible_alert_count"],
                started_at=summary["started_at"],
                already_running=True,
            )

    try:
        started = start_processing_run(dataset_id)
    except ImportError_ as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return StartRunResponse(**started)


@router.get("/{dataset_id}/runs/{run_id}/summary", response_model=RunStatsSummary)
def get_run_summary_endpoint(dataset_id: str, run_id: str) -> RunStatsSummary:
    """Restart-safe, works before/during/after processing — unlike the
    `processing_runs` row's own cached counts (only authoritative once a
    run is 'completed'), this always recomputes from the actually
    persisted `processing_results`/`processing_groups` rows via the same
    shared aggregation function `complete_run`'s final numbers come from
    (repository.compute_run_statistics), so a live snapshot and the final
    persisted summary can never define a count differently.
    """
    try:
        registry.get(dataset_id)
    except DatasetNotFoundError:
        raise _not_found(dataset_id)

    run_row = repository.load_run_summary(run_id, dataset_id)
    if run_row is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found for dataset {dataset_id!r}.")

    stats = repository.compute_run_statistics(run_id, dataset_id) or {
        "processed_count": 0, "workflow_status_counts": {}, "unique_count": 0, "duplicate_count": 0,
        "uncertain_count": 0, "group_count": 0, "grouped_alert_count": 0, "unique_grouped_alert_count": 0,
        "standalone_count": 0,
    }
    return RunStatsSummary(
        dataset_id=dataset_id,
        run_id=run_id,
        status=run_row["status"],
        eligible_alert_count=run_row["eligible_alert_count"],
        processed_count=stats["processed_count"],
        unique_count=stats["unique_count"],
        duplicate_count=stats["duplicate_count"],
        uncertain_count=stats["uncertain_count"],
        group_count=stats["group_count"],
        grouped_alert_count=stats["grouped_alert_count"],
        unique_grouped_alert_count=stats["unique_grouped_alert_count"],
        standalone_count=stats["standalone_count"],
        incident_count=run_row["incident_count"],
        workflow_status_counts=stats["workflow_status_counts"],
        started_at=run_row["started_at"],
        completed_at=run_row["completed_at"],
        last_progress_at=run_row["last_progress_at"],
        error_summary=run_row["error_summary"],
    )


@router.get("/{dataset_id}/runs/{run_id}/recent", response_model=list[ProcessingResult])
def get_run_recent_results_endpoint(dataset_id: str, run_id: str, limit: int = 20) -> list[ProcessingResult]:
    """A small, bounded slice of the most-recently-processed results for
    live display (section 8: "a large result set is not transferred on
    every polling request") — the existing full GET .../runs/{run_id}
    endpoint above is unchanged and still returns everything, preserved
    for compatibility.
    """
    try:
        registry.get(dataset_id)
    except DatasetNotFoundError:
        raise _not_found(dataset_id)
    if repository.load_run_summary(run_id, dataset_id) is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found for dataset {dataset_id!r}.")
    bounded_limit = max(1, min(limit, 200))
    return repository.load_recent_run_results(run_id, dataset_id, bounded_limit)


@router.get("/{dataset_id}/runs/{run_id}", response_model=list[ProcessingResult])
def get_run_endpoint(dataset_id: str, run_id: str) -> list[ProcessingResult]:
    """Restart-safe: an in-memory `record.runs` entry (if this process
    already holds it) is used as a fast path, but a run from BEFORE a
    backend restart — when `record.runs` comes back empty — is retrieved
    from SQLite instead of 404ing just because memory was lost.
    """
    try:
        record = registry.get(dataset_id)
    except DatasetNotFoundError:
        raise _not_found(dataset_id)
    run = record.runs.get(run_id)
    if run is not None:
        return run.results
    results = repository.load_run_results(run_id, dataset_id)
    if results is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found for dataset {dataset_id!r}.")
    return results


@router.get("/{dataset_id}/runs/{run_id}/groups", response_model=list[GroupDetail])
def get_run_groups_endpoint(dataset_id: str, run_id: str) -> list[GroupDetail]:
    """Returns this run's persisted incident groups and their member
    alerts — restart-safe by construction: group membership comes from
    `processing_groups`, each member's disposition/duplicate_of/
    workflow_status comes from its own `processing_results` row (never
    denormalized — see repository.load_results_by_alert_ids), and each
    member's service/severity/timestamp comes from the dataset's
    persisted canonical_alerts (hydrated on restart the same as
    everything else) — never the live in-memory Store, which may be gone.
    """
    try:
        record = registry.get(dataset_id)
    except DatasetNotFoundError:
        raise _not_found(dataset_id)

    # load_run_summary scopes by BOTH run_id and dataset_id together, so a
    # run_id that belongs to a different dataset correctly 404s here
    # rather than leaking that run's data under the wrong dataset.
    if repository.load_run_summary(run_id, dataset_id) is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found for dataset {dataset_id!r}.")

    canonical_by_event_id = {a.event_id: a for a in record.canonical_alerts}
    groups = repository.load_run_groups(run_id)

    details: list[GroupDetail] = []
    for group in groups:
        member_ids = group["member_alert_ids"]
        results_by_id = repository.load_results_by_alert_ids(run_id, dataset_id, member_ids)
        members: list[GroupMember] = []
        redundant_count = 0
        for alert_id in member_ids:
            result = results_by_id.get(alert_id)
            canonical = canonical_by_event_id.get(alert_id)
            disposition = result.disposition if result is not None else None
            if disposition == "redundant":
                redundant_count += 1
            members.append(
                GroupMember(
                    alert_id=alert_id,
                    service=canonical.service_name if canonical is not None else None,
                    severity=canonical.severity.value if canonical is not None else None,
                    timestamp=canonical.timestamp.isoformat() if canonical is not None else None,
                    workflow_status=result.workflow_status.value if result is not None else None,
                    disposition=disposition,
                    duplicate_of=result.duplicate_of if result is not None else None,
                )
            )
        details.append(
            GroupDetail(
                group_id=group["group_id"],
                dataset_id=dataset_id,
                run_id=run_id,
                size=group["size"],
                unique_member_count=group["size"] - redundant_count,
                redundant_member_count=redundant_count,
                members=members,
                incident=group["incident"],
            )
        )
    return details


@router.post("/{dataset_id}/runs/{run_id}/evaluate")
def evaluate_run_endpoint(dataset_id: str, run_id: str) -> dict:
    """Evaluates a run against this dataset's OWN ground-truth labels, if
    the user supplied any (never required). Returns NOT MEASURED when none
    exist — never a fabricated score.

    Uses the run's PERSISTED group membership (processing_groups), not
    whatever the live in-memory Store happens to currently hold — the live
    store may since have been rebuilt by a later run, or may be empty
    entirely after a restart. This makes evaluation restart-safe and
    correctly scoped to the specific run being evaluated, not "whatever
    the store looks like right now".
    """
    try:
        record = registry.get(dataset_id)
    except DatasetNotFoundError:
        raise _not_found(dataset_id)
    run_summary = repository.load_run_summary(run_id, dataset_id)
    if run_id not in record.runs and run_summary is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found for dataset {dataset_id!r}.")

    if not record.ground_truth_labels:
        return {
            "provenance": "NOT MEASURED",
            "reason": "This dataset has no ground-truth labels file (optional, never required for operational use).",
        }

    truth_pairs: set[frozenset[str]] = set()
    by_incident: dict[str, list[str]] = {}
    for label in record.ground_truth_labels:
        by_incident.setdefault(label.true_incident_id, []).append(label.alert_event_id)
    for members in by_incident.values():
        for a, b in combinations(sorted(members), 2):
            truth_pairs.add(frozenset({a, b}))

    predicted_pairs: set[frozenset[str]] = set()
    for group in repository.load_run_groups(run_id):
        ids = sorted(group["member_alert_ids"])
        for a, b in combinations(ids, 2):
            predicted_pairs.add(frozenset({a, b}))

    m = alert_pair_precision_recall(predicted_pairs, truth_pairs)
    return {
        "provenance": "EVALUATION DATA",
        "alert_pair_precision_pct": m.precision_pct,
        "alert_pair_recall_pct": m.recall_pct,
        "true_positives": m.true_positives,
        "false_positives": m.false_positives,
        "false_negatives": m.false_negatives,
        "ground_truth_incident_count": len(by_incident),
    }
