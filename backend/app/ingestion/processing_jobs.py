"""Phase 4: a small, local-only background worker for dataset processing
runs.

This is explicitly NOT a distributed job queue and carries none of that
reliability: it's a single-process, bounded-concurrency ThreadPoolExecutor,
appropriate only for this development-scale application. If the backend
process exits or restarts, any in-flight job is gone along with it — see
dataset_registry.DatasetRegistry.hydrate_from_db and
repository.mark_stale_running_runs_interrupted for how that's detected on
the next startup and reported honestly (status becomes "interrupted"
with an explanatory error_summary) rather than left looking "running"
forever.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

logger = logging.getLogger("bugbusters.processing_jobs")

# A small, FIXED concurrency limit — not an unbounded task queue, and not
# one thread per request. Dataset processing is CPU-bound Python; a
# concurrent run per dataset is already prevented by _prepare_new_run's
# guard, so this cap only bounds how many DIFFERENT datasets can process
# at once.
MAX_CONCURRENT_RUNS = 2
_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_RUNS, thread_name_prefix="run-worker")


def submit_run(dataset_id: str, run_id: str, started_at: datetime, ordered_alerts: list) -> None:
    """Hands an already-persisted (status='running') run off to the
    background executor. The caller (import_service.start_processing_run)
    has already validated preconditions and created the run row — this
    function only ever executes the processing itself.
    """
    from . import import_service  # deferred: avoids a circular top-level import

    def _job() -> None:
        try:
            import_service._execute_processing_run(dataset_id, run_id, started_at, ordered_alerts)
        except Exception:  # noqa: BLE001 — _execute_processing_run already persists a 'failed' status for
            # any processing/persistence error; this is only a last-resort
            # log so a truly unexpected exception (e.g. one raised while
            # persisting the failure state itself) isn't silently lost.
            logger.exception(
                "Background processing run %s for dataset %s raised unexpectedly", run_id, dataset_id
            )

    _executor.submit(_job)
