"""The dataset registry: makes a dataset an explicit, isolated entity
rather than records merged into one global store.

Backward compatibility is load-bearing here: the existing `app.store.store`
/ `app.orchestrator.orchestrator` singletons (used directly by many
existing tests and by milestones 1-3) are wrapped, unmodified, as the
"default" dataset (id="default", provenance="synthetic_demo"). Nothing
about their behavior changes. Every router that should reflect the
currently-selected dataset resolves `registry.get_active()` instead of
importing those singletons directly — see routers/incidents.py etc.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .models import ServiceDependency
from .orchestrator import Orchestrator
from .persistence import repository
from .schemas.canonical import (
    CanonicalAlert,
    CanonicalDependency,
    CanonicalHistoricalIncident,
    CanonicalLog,
    CanonicalMetric,
    GroundTruthLabel,
)
from .schemas.dataset import (
    DatasetFileInfo,
    DatasetLifecycleState,
    DatasetProvenance,
    DatasetSummary,
    FileMapping,
    ValidationReport,
)
from .schemas.agent_results import ProcessingResult
from .store import Store


@dataclass
class ReplayRun:
    id: str
    dataset_id: str
    started_at: datetime
    results: list[ProcessingResult] = field(default_factory=list)


@dataclass
class DatasetRecord:
    id: str
    name: str
    provenance: DatasetProvenance
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    lifecycle_state: DatasetLifecycleState = "imported"

    files: dict[str, DatasetFileInfo] = field(default_factory=dict)
    raw_file_bytes: dict[str, bytes] = field(default_factory=dict)
    file_mappings: dict[str, FileMapping] = field(default_factory=dict)
    validation_report: Optional[ValidationReport] = None

    canonical_alerts: list[CanonicalAlert] = field(default_factory=list)
    canonical_dependencies: list[CanonicalDependency] = field(default_factory=list)
    canonical_logs: list[CanonicalLog] = field(default_factory=list)
    canonical_metrics: list[CanonicalMetric] = field(default_factory=list)
    canonical_historical_incidents: list[CanonicalHistoricalIncident] = field(default_factory=list)
    ground_truth_labels: list[GroundTruthLabel] = field(default_factory=list)

    store: Optional[Store] = None
    orchestrator: Optional[Orchestrator] = None
    runs: dict[str, ReplayRun] = field(default_factory=dict)

    def record_counts(self) -> dict[str, int]:
        return {
            "alerts": len(self.canonical_alerts),
            "dependencies": len(self.canonical_dependencies),
            "logs": len(self.canonical_logs),
            "metrics": len(self.canonical_metrics),
            "historical_incidents": len(self.canonical_historical_incidents),
            "ground_truth": len(self.ground_truth_labels),
        }

    def summary(self, active_id: str) -> DatasetSummary:
        return DatasetSummary(
            id=self.id,
            name=self.name,
            provenance=self.provenance,
            created_at=self.created_at,
            lifecycle_state=self.lifecycle_state,
            files=list(self.files.values()),
            record_counts=self.record_counts(),
            validation_report=self.validation_report,
            is_active=(self.id == active_id),
        )


class DatasetNotFoundError(KeyError):
    pass


class DatasetRegistry:
    def __init__(self) -> None:
        self.datasets: dict[str, DatasetRecord] = {}
        self.active_dataset_id: str = ""
        self._register_default_dataset()

    def _register_default_dataset(self) -> None:
        from . import store as store_module
        from . import orchestrator as orchestrator_module

        default_deps_count = len(store_module.store.dependencies)
        record = DatasetRecord(
            id="default",
            name="Bug Busters Synthetic Fixtures",
            provenance="synthetic_demo",
            lifecycle_state="loaded",
            store=store_module.store,
            orchestrator=orchestrator_module.orchestrator,
        )
        record.canonical_dependencies = [
            CanonicalDependency(
                source_service=d.service,
                target_service=d.depends_on,
                dataset_id="default",
                source_ref=_default_source_ref(i),
            )
            for i, d in enumerate(store_module.store.dependencies)
        ]
        assert len(record.canonical_dependencies) == default_deps_count
        self.datasets["default"] = record
        self.active_dataset_id = "default"

    def new_dataset_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def register(self, record: DatasetRecord) -> None:
        self.datasets[record.id] = record

    def get(self, dataset_id: str) -> DatasetRecord:
        record = self.datasets.get(dataset_id)
        if record is None:
            raise DatasetNotFoundError(dataset_id)
        return record

    def get_active(self) -> DatasetRecord:
        return self.datasets[self.active_dataset_id]

    def set_active(self, dataset_id: str) -> None:
        self.get(dataset_id)  # raises DatasetNotFoundError if missing
        # Persist BEFORE mutating in-memory state — same discipline as
        # import_service.py's confirm_mapping/validate_dataset/load_dataset:
        # a failed write must never leave the app silently serving a
        # different active dataset than what's durably recorded.
        repository.save_active_dataset(dataset_id)
        self.active_dataset_id = dataset_id

    def list_summaries(self) -> list[DatasetSummary]:
        return [r.summary(self.active_dataset_id) for r in self.datasets.values()]

    def hydrate_from_db(self) -> None:
        """Restores every previously-imported dataset (and the active-
        dataset selection) from SQLite — called once from FastAPI's
        startup event, after this module's own import-time construction
        has already registered the in-memory "default" synthetic-demo
        dataset. Never touches or overwrites that default dataset; a
        persisted uploaded dataset can never collide with it since
        import_dataset() always mints a fresh uuid-based id (see
        new_dataset_id()), never "default".

        Pipeline runtime state (Store/Orchestrator) is deliberately NOT
        reconstructed here — see import_service.load_dataset()'s
        docstring. A restored dataset comes back exactly as far as its
        last persisted lifecycle_state, ready for an explicit /validate
        and /load call using its restored raw file bytes and mappings.
        """
        for record in repository.load_all_datasets():
            self.datasets[record.id] = record
        persisted_active = repository.load_active_dataset_id()
        if persisted_active and persisted_active in self.datasets:
            self.active_dataset_id = persisted_active

        # Phase 4 (section 4D): any run still 'running' at this point
        # belonged to a PREVIOUS process — this fresh process has no
        # worker thread for it, so it must never be shown as healthy or
        # actively progressing. Mark it 'interrupted' rather than
        # silently leaving (or claiming) it completed.
        interrupted = repository.mark_stale_running_runs_interrupted()
        if interrupted:
            import logging

            logging.getLogger("bugbusters.startup").warning(
                "Marked %d run(s) as interrupted on startup (no worker exists for them in this process): %s",
                len(interrupted), interrupted,
            )


def _default_source_ref(index: int):
    from .schemas.canonical import SourceRef

    return SourceRef(dataset_id="default", file_name="fixtures.py", row_index=index)


registry = DatasetRegistry()
