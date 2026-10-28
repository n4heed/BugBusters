# Bug Busters // AIOps Command Center

AI-powered alert correlation and incident investigation dashboard. Dark neo-brutalist UI, built for Problem Statement AO-01, Table T-08.

## Stack

- **Frontend**: React + Vite + TypeScript (`frontend/`)
- **Backend**: FastAPI + Pydantic + WebSockets (`backend/`)

The repository was empty when this project started, so both sides were built from scratch to a shared contract (types, REST endpoints, `/ws/alert-stream`).

## Running it

### Frontend only (demo mode)

The frontend works standalone: it tries the backend at startup, and falls back to clearly-labeled synthetic demo data (`DEMO DATA` badges) if the backend isn't reachable.

```bash
cd frontend
npm install
npm run dev
```

Opens at `http://localhost:5173`.

### Backend (FastAPI)

Requires a real Python 3.10+ installation (not the Windows Store stub alias).

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Runs at `http://localhost:8000`. CORS is pre-configured for `http://localhost:5173`.

With both running, the frontend header's `BACKEND` pill will show `REACHABLE` instead of `UNREACHABLE`. The `DATA: SYNTHETIC DEMO` pill stays shown either way — this prototype has no production alert source wired in at all, so a reachable backend is never presented as proof of real production data (see "Agent pipeline" below for why).

### Configuring the API base URL

Copy `frontend/.env.example` to `frontend/.env` and adjust `VITE_API_BASE_URL` if the backend runs somewhere other than `http://localhost:8000`.

### Testing the AI provider adapter with your own API key (opt-in, one request)

After configuring `backend/.env` (see "Selective AI integration" above and the exact variable list below), you can run a single real request against your provider to confirm the adapter actually works — this is never run as part of a normal `pytest` invocation:

```bash
cd backend
$env:RUN_LIVE_LLM_TEST = "1"          # PowerShell; bash: export RUN_LIVE_LLM_TEST=1
pytest tests/test_live_provider_integration.py -v -s
```

It makes exactly one call (spending one unit of `LLM_MAX_CALLS_PER_RUN`), prints the provider/model/latency/token usage it observed, and asserts only that the response is structurally valid and references no alert IDs beyond the two tiny synthetic ones it supplied — never the model's specific wording.

### Running the backend test suite

```bash
cd backend
pip install -r requirements.txt
pytest
```

Covers the Triage/Investigation/Verification agents, the orchestrator, and the deterministic replay scenario (`backend/tests/test_pipeline.py`), plus selective-AI-integration tests — budget enforcement, bounded context, ground-truth exclusion, invented-reference rejection, AI-disabled-by-default (`backend/tests/test_ai_pipeline.py`), all against a mocked provider; no real API calls or credentials are used by any automated test. **These tests were written but not executed in the environment this was built in** — see "Known limitations."

## Agent pipeline

As of the second milestone, alerts no longer just sit in static fixtures — they're run through a real, deterministic, three-agent pipeline before anything is shown as a candidate incident:

```
Alert Ingestion → Normalization → Triage Agent → Investigation Agent → Verification Agent → Incident/Safety Queue → Human Review → SEA Memory
```

- **Normalization** (`backend/app/services/normalization.py`): validates required fields, normalizes timestamps to UTC, generates a stable ID and a service+signal fingerprint. A payload missing `service` or `signal` is never silently dropped — it's preserved as a flagged, unclustered record (`_handle_malformed` in `orchestrator.py`).
- **Triage Agent** (`backend/app/agents/triage_agent.py`): deterministic, explainable weighted-signal correlation (`backend/app/services/correlation.py`) — temporal proximity, shared service, modeled dependency path, and fingerprint similarity, each with its own documented weight and a 0.50 grouping threshold. Also handles duplicate detection (same fingerprint within 30 minutes) and bootstraps brand-new candidate groups from two correlated alerts rather than ever merging alerts "because they're close in time" alone.
- **Investigation Agent** (`backend/app/agents/investigation_agent.py`): builds a probable-cause hypothesis strictly from the evidence gathered in `backend/app/services/evidence_retrieval.py` (actual alert records + the supplied dependency topology — nothing invented). Returns `insufficient_evidence` explicitly rather than fabricating confidence when fewer than 2 alerts or no plausible origin exists. No LLM provider is configured in this environment (verified — there was none in the repo before this milestone, and `backend/app/llm_provider.py` reads credentials from env vars only); it always runs the deterministic fallback and is transparent about that (`evidence_source: "deterministic_fallback"`).
- **Verification Agent** (`backend/app/agents/verification_agent.py`): checks every referenced alert ID actually exists in the store, every dependency edge the Investigation Agent cited actually exists in the supplied topology, and timestamps are internally consistent. A rejection or a pending outcome never deletes the source alerts — the candidate incident is still created and shown, just flagged `REJECTED`/`PENDING_HUMAN_REVIEW` instead of `VERIFIED`.
- **Orchestrator** (`backend/app/orchestrator.py`): runs all three stages for one alert, is idempotent against re-processing the same alert ID, and registers the alert in the store *before* any agent runs — so an agent exception can never make an alert disappear (caught, recorded as `PROCESSING_ERROR`, alert stays visible).

### Exercising it

- `POST /api/pipeline/ingest` — run one raw alert through the pipeline.
- `POST /api/pipeline/replay` — runs the **deterministic replay scenario** (`backend/app/fixtures_pipeline.py`): 6 fixed alerts with explicit known expected outcomes (a 3-alert group that should merge, a duplicate, a genuinely novel alert, and an ambiguous one-weak-signal alert that should NOT merge). This is the main correctness demonstration — reproducible, not dependent on the random WebSocket simulator.
- `GET /api/pipeline/evaluation` — the alert-pair precision/recall/verification-failure-rate/latency numbers from the last replay run, computed against that scenario's explicit synthetic ground truth (`evaluated_on: "synthetic_fixture_ground_truth"`). This is a 6-alert sanity check, not a benchmark against a real dataset.
- The WebSocket simulator (`/ws/alert-stream`, `start_simulation`) now also runs each synthetic alert through the same orchestrator and emits a second `processing_update` message alongside each `alert` message, carrying its `workflow_status`.
- The frontend's **Active Incidents** tab has a new "Agent Pipeline // Deterministic Replay" panel at the top with a **Run Replay** button that calls `/api/pipeline/replay` directly and renders the per-alert results and evaluation metrics table. It's disabled (with an honest explanation) when the backend isn't reachable, since demo-fixture mode has no orchestrator to call.

## Selective AI integration

A model is called only when the deterministic pipeline genuinely can't decide confidently — never automatically for every alert, never with the whole dataset. **Disabled by default** (`LLM_ENABLED=false`); the deterministic pipeline is fully functional with zero AI configuration.

### Configuring a provider

```bash
cd backend
cp .env.example .env
# edit .env: set LLM_ENABLED=true, LLM_PROVIDER=openai, LLM_MODEL=..., LLM_API_KEY=...
```

`.env` is gitignored. The key is read only inside `backend/app/ai/provider.py` at call time, is never logged, never placed on any response object, and never reaches the frontend (there is no AI configuration of any kind in `frontend/`). An invalid/incomplete AI config doesn't crash the app — it falls back to AI-disabled and logs the (secret-free) config summary at startup.

### When AI is actually called (`backend/app/ai/selection_policy.py`)

The deterministic Triage → Investigation pass **always** runs first. A model is consulted afterward only when:
- an operator explicitly requests it (`force_ai: true` on `POST /api/pipeline/ingest`), or
- the deterministic investigation found *some* evidence but couldn't form a confident hypothesis (`insufficient_evidence` with ≥1 observed fact), or
- the correlation score only narrowly cleared the grouping threshold (0.50–0.65) — a genuinely ambiguous grouping.

Never for: schema validation, timestamp conversion, duplicate detection, confidently-grouped incidents, KPI computation, or display formatting — those never reach the selection policy at all.

### Bounded, redacted, ground-truth-safe context (`backend/app/ai/evidence.py`)

Every model call sends an `EvidencePackage`, never raw data: at most `LLM_MAX_ALERTS_PER_INCIDENT` alerts (oldest-first truncation, omitted count recorded) and `LLM_MAX_HISTORICAL_CASES` historical cases — and a historical case is only offered if it was human-approved **strictly before** the earliest alert in the current group, so a group's own eventual outcome can never leak back into its own investigation. `EvidenceAlertRef` has no host/IP/customer fields at all — there's nothing to accidentally send.

### Budget, retries, usage (`backend/app/ai/budget.py`, `ai/usage_tracker.py`)

A shared `ModelCallBudget` (`LLM_MAX_CALLS_PER_RUN`) is checked before every call, including a malformed-response repair attempt; exhaustion returns `AI_BUDGET_EXHAUSTED` and falls back to the deterministic result rather than blocking. Timeouts/5xx get up to `LLM_MAX_RETRIES` bounded retries; auth failures never retry. Every call's provider, purpose, alert/case counts, tokens (labelled `tokens_are_estimated` when the provider doesn't report them), latency, retries, and status are recorded in memory (`GET /api/pipeline/ai/usage`) — no prompts, no credentials. No cost figure is ever computed or shown, since no pricing table is wired in for this milestone.

### Validation

The model's response is schema-validated (`ai/schemas.py: ModelInvestigationResponse`), and every `supporting_evidence_ids`/`historical_case_ids` reference is checked against what was actually sent — an invented reference is treated as a failed call (`AI_CALL_FAILED`) and falls back to the deterministic result, never trusted.

### What the UI shows

Every incident now reports `ai_status` (`AI_NOT_RUN` / `AI_DISABLED` / `AI_CALLED` / `AI_BUDGET_EXHAUSTED` / `AI_CALL_FAILED`) and the reason, in the Investigation Agent panel — never a fabricated confidence percentage. The Agent Pipeline replay panel additionally shows live budget usage and token/latency numbers when AI is enabled.

## Endpoints implemented

REST (prefix `/api`):

- `GET /api/health`
- `GET /api/incidents` — summaries for the incident feed
- `GET /api/incidents/{id}` — full incident detail (evidence, dependency graph, safety disclosure, telemetry)
- `POST /api/incidents/{id}/approve` — approve a grouping, writes a SEA Memory entry
- `GET /api/incidents/safety-queue/alerts`
- `GET /api/tuning` — tuning recommendations
- `POST /api/tuning/{id}/approve`, `POST /api/tuning/{id}/reject`
- `GET /api/sea-memory` — the audit log
- `GET /api/metrics` — headline KPI values (incident recall / grouping precision are returned as `null` — "not measured" — against the full demo dataset, since no ground-truth evaluation harness exists for it)
- `POST /api/pipeline/ingest` (accepts `force_ai: bool`), `POST /api/pipeline/replay`, `GET /api/pipeline/evaluation` — see "Agent pipeline" above
- `GET /api/pipeline/ai/status`, `GET /api/pipeline/ai/usage` — see "Selective AI integration" below

WebSocket:

- `ws://localhost:8000/ws/alert-stream` — connecting sends nothing by itself. Client sends `{"action": "start_simulation"}` to begin receiving synthetic, clearly-marked (`is_synthetic: true`) alert events, and `{"action": "stop_simulation"}` to stop. There is no real alert source wired in; this is an explicit, user-triggered demo stream, not production telemetry.

## Demo data integrity

All synthetic fixture data (`backend/app/fixtures.py` and `frontend/src/data/fixtures.ts`, kept in sync) reconciles to exactly **420 raw alerts**:

- 23 alerts across 3 correlated incidents (12 + 6 + 5)
- 1 independent safety-queue alert
- 396 recurring alerts across 6 tuning rule groups (68 + 64 + 71 + 65 + 63 + 65)

Notification-reduction is defined explicitly (no official definition exists for this challenge): each correlated incident plus each standalone safety alert counts as one notification, measured against raw alert count. Tuning recommendations are review summaries, not paging notifications, so they're excluded from that count. Incident recall and grouping precision are reported as "not measured" rather than fabricated, since no labelled ground truth exists.

## What's demo-only vs. real

- Incident/tuning **approvals** call the real backend when it's reachable; the backend writes a SEA Memory audit entry and marks the result `demo_only: true` in its response (`APPROVED IN DEMO — NOT APPLIED TO PRODUCTION`), since there's no real production alerting system behind this prototype either way.
- The **alert stream** backend has no real ingestion source — only the explicit, synthetic `start_simulation` demo stream described above.
- **Redaction** (host/IP/customer) happens server-side in the backend before data is ever serialized to JSON; the frontend's masking in the telemetry table is a presentation safeguard on top of that, not the source of truth.

## Evaluation & reliability (Milestone 3)

**Unlike the first two milestones, this one was actually executed.** A real Python 3.12 (the official embeddable distribution from python.org, no system install) was set up specifically to run it — see "Verifying it yourself" below if you want to reproduce this.

### Modes

- **Mode A — synthetic smoke tests** (`backend/app/evaluation/smoke.py`): 9 fast, deterministic checks covering related/unrelated/repeated/novel/ambiguous alerts, missing telemetry, inconsistent (cyclic) dependency data, and malformed payloads. **Actually run: 9/9 passed.**
- **Mode B — challenge dataset benchmark**: no real dataset exists anywhere in this project (checked). `backend/app/evaluation/dataset_loader.py` implements the loading interface and documents exactly what's missing; calling it raises `DatasetUnavailableError` rather than fabricating a result. Reported as `NOT MEASURED`.
- **Mode C — selective AI evaluation**: no live model credentials exist in this environment. Routine comparisons use a mocked provider (see baseline 4 below); a live-model sample is reported as `NOT MEASURED`, not invented.

### Four baselines, identical 6-alert input, actually run

| Baseline | Notifications | Precision | Recall | AI calls | Latency (ms) |
|---|---:|---:|---:|---:|---:|
| `no_grouping` | 6 | N/A | 0% | 0 | 0 |
| `exact_dedup` | 5 | 0% | 0% | 0 | 0 |
| `deterministic_correlation` | 3 | **100%** | **100%** | 0 | 0.571 |
| `selective_hybrid` (mocked AI) | 3 | **100%** | **100%** | 2 | 0.645 |

`exact_dedup`'s 0% precision is a real, documented quirk of the strict pairwise metric, not a bug: its one predicted pair is a true duplicate merge (correct behavior) that simply isn't in the ground-truth *incident* pairs, so it scores as a false positive under the pairwise definition. `selective_hybrid` made 2 real (mocked) model calls — for the two investigations whose correlation score (0.60) fell in the "ambiguous" 0.50–0.65 band — but produced **identical grouping metrics** to the deterministic baseline. This is architectural, not coincidental: AI only assists the Investigation stage, which runs *after* Triage has already decided group membership, so it cannot change which alerts get grouped in this design. Confirmed by `test_baseline_hybrid_does_not_change_grouping_vs_deterministic`.

### Threshold calibration — not performed, by design

The only labelled data in this project is the same 6-alert scenario every result above is reported against. Tuning the 0.50 correlation threshold against it and reporting the result as unbiased would be exactly the test-set leakage this milestone prohibits. The threshold is **unchanged**. A diagnostic sweep tool exists (`backend/app/evaluation/calibration.py`) and was run as a sanity check, not a selection procedure — real output: precision/recall at threshold 0.2 is 50%/100% (over-merges), at 0.5 (current) is 100%/100%, at 0.8 is N/A/0% (misses everything). This confirms 0.50 is well-chosen for this scenario without having tuned against it.

### A real, documented reliability finding

Building and running the out-of-order-arrival failure-injection test surfaced a genuine bug: **grouping is arrival-order sensitive.** A bootstrap-formed candidate group never re-scores a previously-unclustered "straggler" alert against it after the group forms — so if alerts for one real incident arrive in certain orders, one of them can get permanently stranded in the safety queue. Reproduced, documented, and locked in as an `xfail` regression test (`tests/test_failure_injection.py::test_out_of_order_arrival_still_groups_correctly`) and as a standing finding in the evaluation report's error analysis, rather than hot-fixed under time pressure — see "Known limitations" below for the recommended fix.

### Two other real bugs found and fixed by actually running this milestone

1. **Investigation Agent accepted invalid model responses.** `model_response` was assigned from the provider call *before* its references were validated; when validation then rejected an invented alert/case ID, the exception handler ran but the already-assigned (invalid) response was never cleared, so it was used anyway instead of falling back. Fixed — confirmed by `test_invented_evidence_reference_is_rejected_and_falls_back`, which failed before the fix and passes after it.
2. **The calibration sweep tool was a silent no-op.** `triage_agent.py` imported `GROUPING_THRESHOLD` by value (`from ... import GROUPING_THRESHOLD`) instead of reading it off the module, so mutating `correlation.GROUPING_THRESHOLD` at runtime (as the sweep does) had no effect — confirmed identical output at thresholds 0.2/0.5/0.8 before the fix. Fixed by reading the threshold via `correlation.GROUPING_THRESHOLD` at call time.

### Verifying it yourself

```bash
cd backend
pip install -r requirements.txt
pytest -v   # 50 passed, 1 xfailed, as of this milestone
uvicorn app.main:app --reload
# then: curl -X POST http://localhost:8000/api/evaluation/report
```

## Generic data ingestion & multi-dataset support (Milestone 4)

**Audit finding:** no `C:\mdata` path, or any hardcoded dataset path, existed anywhere in this repository before this milestone (checked by grepping the whole source tree). The previous "fixture" was always pure Python code (`fixtures.py`), never a filesystem dependency — so the specific concern this milestone opens with didn't apply here. Effort went entirely into building the generic ingestion layer itself.

### Architecture

```
backend/app/
├── ingestion/
│   ├── base.py              — adapter interface, format detection
│   ├── csv_adapter.py, json_adapter.py, jsonl_adapter.py
│   ├── zip_bundle_adapter.py — safe extraction (path traversal / zip-bomb guarded)
│   ├── schema_detector.py   — bounded preview + field/timestamp/ID detection
│   ├── field_mapping.py     — synonym tables, category classification
│   ├── normalization.py     — confirmed-mapping → canonical records
│   ├── validation.py        — never silently drops a record
│   └── import_service.py    — ties it all together (Stages A-F)
├── schemas/canonical.py     — CanonicalAlert/Metric/Log/Dependency/HistoricalIncident/GroundTruthLabel
├── schemas/dataset.py       — Dataset entity, FileMapping, ValidationReport
└── dataset_registry.py      — makes a dataset an explicit, isolated entity
```

The three agents still only ever consume the same raw-dict shape they always did (`{id, timestamp, service, signal, severity}`) — `ingestion/import_service.canonical_alert_to_raw()` is the one place a `CanonicalAlert` is translated into it, so Triage/Investigation/Verification never depend on CSV columns or source-specific JSON keys.

### Supported formats (tested, not just claimed)

CSV, JSON (array, single object, or `{alerts:[...]}`/`{data:[...]}`/etc. wrapper), JSONL/NDJSON, and ZIP bundles of the above. Parquet or any other format is explicitly **not** supported — no adapter exists for it, and importing one fails clearly (`UnsupportedFormatError`) rather than pretending to succeed.

### Example field mappings (from real, tested datasets)

| Source field | Canonical field | Dataset |
|---|---|---|
| `event_time` | `timestamp` | Dataset 2 (renamed CSV) |
| `application` | `service_name` | Dataset 2 |
| `level` | `severity` | Dataset 2 |
| `alert_name` | `rule_name` | Dataset 2 |
| `host` | `resource_id` | Dataset 2 |
| `t` | `timestamp` | Dataset 3 (ambiguous — user-confirmed, not guessed) |
| `svc` | `service_name` | Dataset 3 |
| `desc` | `message` | Dataset 3 |

### Dataset isolation

Each dataset — including the pre-existing demo fixtures, now wrapped as dataset `"default"` — gets its own `Store`/`Orchestrator` pair (reusing the exact same classes from milestones 1-3, just with `seed_demo_fixtures=False` for imported data). Switching the active dataset (`POST /api/datasets/{id}/select`) changes what `/api/incidents`, `/api/tuning`, `/api/sea-memory`, `/api/metrics` and the live WebSocket simulation all show — verified by `test_switching_active_dataset_changes_what_routers_see` and `test_datasets_remain_isolated_from_each_other`.

### API endpoints

`POST /api/datasets/import` (multipart, accepts multiple files or a `.zip`), `GET /api/datasets`, `GET /api/datasets/{id}`, `GET /api/datasets/{id}/schema`, `POST /api/datasets/{id}/mapping`, `POST /api/datasets/{id}/validate`, `POST /api/datasets/{id}/load`, `POST /api/datasets/{id}/select`, `POST /api/datasets/{id}/replay`, `GET /api/datasets/{id}/runs/{run_id}`, `POST /api/datasets/{id}/runs/{run_id}/evaluate`.

### Frontend

A new **Datasets** tab: import form (multi-file + `.zip`), dataset list with provenance/state/record-count/active indicator, per-file schema inspection, a field-mapping confirmation UI (category + per-canonical-field source dropdowns), validation report display, and Validate/Load/Replay/Evaluate/Select controls. Verified live against the real backend in the browser.

### Tested with 3 genuinely distinct datasets, for real

`backend/tests/test_ingestion.py` (14 tests, all passing): (1) canonical-field-name JSONL, (2) a CSV with completely different, reordered field names, (3) a JSON file with abbreviated/ambiguous fields (`t`, `svc`, `lvl`, `desc`) that correctly falls back to `category: "unknown"` rather than guessing, requiring explicit user mapping — plus a ZIP bundle (alerts + dependencies, with a non-dataset `README.txt` correctly skipped), path-traversal rejection, malformed-record counting, dataset isolation, and idempotent re-validation.

### Four more real bugs found and fixed by actually running this milestone

1. A low-confidence category guess (e.g. matching only 1 of 9 possible "alerts" fields) was still being silently applied as the file's category instead of falling back to `"unknown"` — exactly the "guessing silently" behavior the milestone prohibits. Fixed: category now stays `"unknown"` unless classification is actually confident.
2. The first confidence-threshold fix (`score >= 3`) unfairly failed small categories like `dependencies` (only 3 possible fields total, so matching 2 of them could never reach 3).
3. The following ratio-based fix (`score/category_size >= 2/3`) then broke the common case: real alert files rarely populate every optional field, so even unambiguous alert data could score below 66%. Fixed properly with explicit "core fields per category" (e.g. alerts needs `timestamp` + `service_name` + at least one of severity/message/rule_name) rather than a one-size-fits-all ratio or count.

### What import does NOT do automatically

Exactly as required: importing a file never calls an LLM, never runs the agent pipeline, never applies a tuning recommendation, and never marks anything human-approved. `validate`, `load`, and `replay` are separate, explicit API calls.

## Known limitations

- **Dataset registry is in-memory, not persistent.** Imported datasets (including their raw file bytes, kept for re-validation) are lost on backend restart, same as the rest of this app's state. Documented, not hidden — see the milestone's "smallest safe extension" guidance.
- **Historical-incident and ground-truth file categories are normalized but not yet wired into the agent pipeline's evidence retrieval** (`ai/evidence.py` still reads historical patterns from the default dataset's SEA memory, not from an imported dataset's `canonical_historical_incidents`). A dataset's own ground-truth labels ARE correctly isolated and used only by `/runs/{run_id}/evaluate`, never leaked to agents.
- The field-mapping UI lets a user confirm mappings one file at a time; there's no "save this mapping as a reusable template for future imports of the same source" feature yet (the milestone lists this as a nice-to-have: "schema mappings can be saved and reused appropriately" — not yet implemented).
- Record counting during schema detection (`schema_detector.py`) does a full pass over the file (bounded by `MAX_COUNT_SCAN = 100,000`), which is cheap at this product's demo scale but would need to switch to a true streaming count for genuinely large files.
- The "connector" architecture is explicitly scoped to file upload only — no live monitoring-system connector (Datadog, Prometheus, etc.) exists or is claimed; `ingestion/base.py`'s `SourceAdapter` interface is the extension point for one, unimplemented.

- **Arrival-order sensitivity in correlation (the highest-priority open issue).** A candidate group formed by bootstrapping two alerts together does not re-score other already-unclustered alerts against it afterward — so a related alert that arrived earlier and didn't win the initial bootstrap pairing can be permanently stranded, depending on arrival order. Reproduced and locked in as `tests/test_failure_injection.py::test_out_of_order_arrival_still_groups_correctly` (`xfail`, `strict=True`) and surfaced in `/api/evaluation/report`'s error analysis. **Recommended fix:** after any group gains a new member, re-score the remaining safety-queue alerts against it (not just at the moment each alert first arrives), bounded to a small recent window to stay cheap.
- No real LLM/model provider has ever been configured or called — `LLM_ENABLED=false` by default, and no credentials exist in this environment. The `OpenAICompatibleProvider` adapter (`backend/app/ai/provider.py`) was written against the OpenAI chat-completions shape but **has never made a real HTTP request**; its httpx error handling, response parsing, and retry classification are untested against an actual API. Mode C (selective AI evaluation) is reported as `NOT MEASURED` for this reason.
- No real challenge dataset exists anywhere in this project (checked: no data files, no dataset references). Mode B (ground-truth benchmark) is reported as `NOT MEASURED`, with the loading interface and exact missing inputs documented in `backend/app/evaluation/dataset_loader.py`.
- The correlation threshold (0.50) was not calibrated in this milestone — see "Threshold calibration" above for why tuning it now would be test-set leakage. A diagnostic sweep tool exists for when real validation data is available.
- There is no persistent database; the Python backend's store (including all pipeline state — candidate groups, processing results) is in-memory only and resets on restart.
- `GET /api/pipeline/evaluation` only reflects the most recent `/api/pipeline/replay` run (6 synthetic alerts) — it is not computed continuously and is not a claim about the full 420-alert demo dataset. `GET /api/evaluation/report` is the full Milestone 3 report and recomputes fresh on every call.
- The frontend has been verified end-to-end in a browser against both the demo fixture fallback and, as of this milestone, **a genuinely running real backend** (see below) — but the two were never run together long enough to exercise the live WebSocket `processing_update` stream end to end in the browser; that remains unverified.
