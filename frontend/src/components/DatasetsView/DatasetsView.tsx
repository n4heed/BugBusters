import { useEffect, useRef, useState } from "react";
import type {
  DatasetSchemaResponse,
  DatasetSummary,
  FieldMappingEntry,
  FileCategory,
  ProcessingResult,
  RunStatsSummary,
  RunSummary,
} from "../../types/domain";
import { api } from "../../api/client";
import { DemoBadge } from "../common/DemoBadge";
import type { Toast } from "../ToastNotifications";
import { ProcessingDashboard } from "./ProcessingDashboard";

const CATEGORY_OPTIONS: FileCategory[] = [
  "alerts",
  "metrics",
  "logs",
  "dependencies",
  "historical_incidents",
  "ground_truth",
  "unknown",
];

const CANONICAL_FIELDS_BY_CATEGORY: Record<string, string[]> = {
  alerts: [
    "timestamp",
    "service_name",
    "severity",
    "rule_name",
    "message",
    "resource_id",
    "source_alert_id",
    "fingerprint",
    "metric_name",
    "metric_value",
    "metric_threshold",
  ],
  metrics: ["timestamp", "service_name", "metric_name", "value", "unit", "threshold"],
  logs: ["timestamp", "service_name", "severity", "message", "correlation_id"],
  dependencies: ["source_service", "target_service", "edge_type"],
  historical_incidents: ["incident_id", "first_seen", "last_seen", "affected_services", "description", "confirmation_status"],
  ground_truth: ["alert_event_id", "true_incident_id"],
  unknown: [],
};

export function DatasetsView({
  backendReachable,
  onPushToast,
  onDatasetSelected,
}: {
  backendReachable: boolean;
  onPushToast: (toast: Omit<Toast, "id">) => void;
  onDatasetSelected: () => void;
}) {
  const [datasets, setDatasets] = useState<DatasetSummary[]>([]);
  const [inspectingId, setInspectingId] = useState<string | null>(null);
  const [schema, setSchema] = useState<DatasetSchemaResponse | null>(null);
  const [importName, setImportName] = useState("");
  const [importFiles, setImportFiles] = useState<File[]>([]);
  const [importing, setImporting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [runHistory, setRunHistory] = useState<RunSummary[]>([]);
  const [evaluation, setEvaluation] = useState<Record<string, unknown> | null>(null);
  const [mappingDraft, setMappingDraft] = useState<Record<string, { category: FileCategory; mappings: Record<string, string> }>>({});

  // --- Phase 4: live, data-driven processing dashboard for the dataset
  // currently being inspected. activeRunId drives the polling loop below;
  // runStats/recentResults always come straight from the backend's
  // persisted processing_results/processing_groups — never a local
  // counter — so they can never drift from what's actually stored.
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [runStats, setRunStats] = useState<RunStatsSummary | null>(null);
  const [recentResults, setRecentResults] = useState<ProcessingResult[]>([]);
  const [dashboardStale, setDashboardStale] = useState(false);
  const pollSeqRef = useRef(0);

  async function refreshDashboard(datasetId: string, runId: string) {
    const mySeq = ++pollSeqRef.current;
    try {
      const [summary, recent] = await Promise.all([
        api.getRunSummary(datasetId, runId),
        api.getRunRecentResults(datasetId, runId, 20),
      ]);
      // A later poll (or a dataset/run switch) may have started and
      // already finished while this request was in flight — discard a
      // stale response rather than overwriting newer state with it.
      if (mySeq !== pollSeqRef.current) return;
      setRunStats(summary);
      setRecentResults(recent);
      setDashboardStale(false);
    } catch {
      if (mySeq !== pollSeqRef.current) return;
      // A failed poll must never fabricate zeros or blank the dashboard —
      // keep showing the last known-good numbers, just flag them as stale.
      setDashboardStale(true);
    }
  }

  // tick() below reads this ref (rather than closing over `runStats`
  // state at effect-setup time) so each poll's own just-fetched status
  // decides whether to schedule the next one.
  const runStatsStatusRef = useRef<string | null>(null);
  const prevTerminalCheckRef = useRef<string | null>(null);
  useEffect(() => {
    runStatsStatusRef.current = runStats?.status ?? null;
    const isTerminal = runStats && runStats.status !== "running";
    const key = runStats ? `${runStats.run_id}:${runStats.status}` : null;
    if (isTerminal && key && key !== prevTerminalCheckRef.current && inspectingId) {
      prevTerminalCheckRef.current = key;
      // The run just reached a terminal state — refresh the dataset list
      // (lifecycle_state changed) and run history table too, not just the
      // live KPI panel, so nothing lags behind what the backend now shows.
      refreshList();
      refreshRunHistory(inspectingId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runStats, inspectingId]);

  useEffect(() => {
    if (!inspectingId || !activeRunId) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function tick() {
      if (stopped) return;
      await refreshDashboard(inspectingId!, activeRunId!);
      if (stopped) return;
      if (runStatsStatusRef.current === "running") {
        timer = setTimeout(tick, 1500);
      }
    }
    tick();

    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inspectingId, activeRunId]);

  async function refreshList() {
    try {
      setDatasets(await api.listDatasets());
    } catch {
      onPushToast({ kind: "error", message: "Could not load the dataset list from the backend." });
    }
  }

  useEffect(() => {
    if (backendReachable) refreshList();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [backendReachable]);

  async function refreshRunHistory(id: string): Promise<RunSummary[]> {
    try {
      // Restart-safe: this always comes from the backend's persisted run
      // history (SQLite), never from in-browser state — so reloading the
      // page, or coming back after a backend restart, still shows prior runs.
      const runs = await api.listDatasetRuns(id);
      setRunHistory(runs);
      return runs;
    } catch {
      setRunHistory([]);
      return [];
    }
  }

  async function inspect(id: string) {
    // Switching datasets must never leave the PREVIOUS dataset's counts or
    // rows visible while the new one loads.
    setInspectingId(id);
    setEvaluation(null);
    setActiveRunId(null);
    setRunStats(null);
    setRecentResults([]);
    setDashboardStale(false);
    try {
      const s = await api.getDatasetSchema(id);
      setSchema(s);
      const draft: Record<string, { category: FileCategory; mappings: Record<string, string> }> = {};
      for (const [fileName, mapping] of Object.entries(s.suggested_mappings)) {
        const m: Record<string, string> = {};
        for (const entry of mapping.field_mappings) m[entry.canonical_field] = entry.source_field;
        draft[fileName] = { category: mapping.category, mappings: m };
      }
      setMappingDraft(draft);
      const runs = await refreshRunHistory(id);
      // Restored on inspect so a run already in progress (or a run from
      // before a backend restart) shows live data immediately, without
      // requiring the user to click "Start Processing" again.
      if (runs.length > 0) setActiveRunId(runs[0].run_id);
    } catch {
      onPushToast({ kind: "error", message: "Could not load this dataset's schema." });
    }
  }

  if (!backendReachable) {
    return (
      <div>
        <h2 className="section-heading">Datasets</h2>
        <p className="loading-text">Requires a reachable backend — unavailable in demo mode.</p>
      </div>
    );
  }

  async function doImport() {
    if (importFiles.length === 0) {
      onPushToast({ kind: "error", message: "Select at least one file (or a .zip bundle) to import." });
      return;
    }
    setImporting(true);
    try {
      const summary = await api.importDataset(importName || "Untitled dataset", importFiles);
      onPushToast({ kind: "success", message: `Imported ${summary.name} — review its schema and confirm field mappings below.` });
      setImportName("");
      setImportFiles([]);
      await refreshList();
      await inspect(summary.id);
    } catch (err) {
      onPushToast({ kind: "error", message: err instanceof Error ? err.message : "Import failed." });
    } finally {
      setImporting(false);
    }
  }

  async function doConfirmMapping(fileName: string) {
    if (!inspectingId) return;
    const draft = mappingDraft[fileName];
    if (!draft) return;
    const fieldMappings: FieldMappingEntry[] = Object.entries(draft.mappings)
      .filter(([, sourceField]) => sourceField)
      .map(([canonicalField, sourceField]) => ({ source_field: sourceField, canonical_field: canonicalField, confidence: "confirmed" }));
    setBusy(true);
    try {
      await api.confirmDatasetMapping(inspectingId, fileName, draft.category, fieldMappings);
      onPushToast({ kind: "success", message: `Mapping confirmed for ${fileName}.` });
      await inspect(inspectingId);
      await refreshList();
    } catch (err) {
      onPushToast({ kind: "error", message: err instanceof Error ? err.message : "Could not confirm mapping." });
    } finally {
      setBusy(false);
    }
  }

  async function doValidate() {
    if (!inspectingId) return;
    setBusy(true);
    try {
      const summary = await api.validateDataset(inspectingId);
      onPushToast({
        kind: summary.validation_report && summary.validation_report.invalid_records > 0 ? "info" : "success",
        message: `Validated: ${summary.validation_report?.valid_records ?? 0} valid, ${summary.validation_report?.invalid_records ?? 0} invalid records.`,
      });
      await refreshList();
      await inspect(inspectingId);
    } catch (err) {
      onPushToast({ kind: "error", message: err instanceof Error ? err.message : "Validation failed." });
    } finally {
      setBusy(false);
    }
  }

  async function doLoad() {
    if (!inspectingId) return;
    setBusy(true);
    try {
      await api.loadDataset(inspectingId);
      onPushToast({ kind: "success", message: "Dataset loaded — ready to run processing (replay)." });
      await refreshList();
    } catch (err) {
      onPushToast({ kind: "error", message: err instanceof Error ? err.message : "Load failed." });
    } finally {
      setBusy(false);
    }
  }

  async function doSelect(id: string) {
    setBusy(true);
    try {
      await api.selectDataset(id);
      onPushToast({ kind: "success", message: "Active dataset switched — dashboard views now reflect it." });
      await refreshList();
      onDatasetSelected();
    } catch (err) {
      onPushToast({ kind: "error", message: err instanceof Error ? err.message : "Could not switch dataset." });
    } finally {
      setBusy(false);
    }
  }

  async function doStartRun() {
    if (!inspectingId) return;
    // The backend itself rejects a second concurrent run for this dataset
    // (and returns the existing one instead) — this guard just avoids an
    // avoidable round trip when the button is clicked repeatedly.
    if (activeRunId && runStats?.status === "running") return;
    setBusy(true);
    try {
      const started = await api.startRun(inspectingId);
      onPushToast({
        kind: "info",
        message: started.already_running
          ? `Processing is already running for this dataset (run ${started.run_id}).`
          : `Processing started (run ${started.run_id}) — live counts below update automatically.`,
      });
      setActiveRunId(started.run_id);
      await refreshDashboard(inspectingId, started.run_id);
      await refreshList();
      await refreshRunHistory(inspectingId);
    } catch (err) {
      onPushToast({ kind: "error", message: err instanceof Error ? err.message : "Could not start processing." });
    } finally {
      setBusy(false);
    }
  }

  async function doManualRefresh() {
    if (!inspectingId || !activeRunId) return;
    await refreshDashboard(inspectingId, activeRunId);
  }

  async function doEvaluate() {
    if (!inspectingId || !activeRunId) return;
    setBusy(true);
    try {
      setEvaluation(await api.evaluateRun(inspectingId, activeRunId));
    } catch (err) {
      onPushToast({ kind: "error", message: err instanceof Error ? err.message : "Evaluation failed." });
    } finally {
      setBusy(false);
    }
  }

  const inspectingSummary = datasets.find((d) => d.id === inspectingId);

  return (
    <div>
      <h2 className="section-heading">Datasets</h2>
      <p className="section-subtitle">
        Import alert/telemetry data from CSV, JSON, JSONL or a .zip bundle, map its fields to the canonical schema,
        validate, load, and run the agent pipeline against it — isolated from every other dataset.
      </p>

      <section className="panel" style={{ marginBottom: 16 }}>
        <h3 className="section-heading" style={{ fontSize: "0.7rem" }}>
          Import a Dataset
        </h3>
        <div className="filter-row">
          <input
            className="filter-input"
            placeholder="Dataset name"
            value={importName}
            onChange={(e) => setImportName(e.target.value)}
          />
          <input
            type="file"
            multiple
            accept=".csv,.json,.jsonl,.ndjson,.zip"
            onChange={(e) => setImportFiles(Array.from(e.target.files ?? []))}
          />
          <button className="btn btn-sm" onClick={doImport} disabled={importing}>
            {importing ? "Importing…" : "Import"}
          </button>
        </div>
        {importFiles.length > 0 && (
          <div className="chip-row">
            {importFiles.map((f) => (
              <span className="chip mono" key={f.name}>
                {f.name} ({Math.round(f.size / 1024)} KB)
              </span>
            ))}
          </div>
        )}
      </section>

      <div className="table-wrap" style={{ marginBottom: 16 }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Provenance</th>
              <th>State</th>
              <th>Records</th>
              <th>Active</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {datasets.map((d) => (
              <tr key={d.id}>
                <td>{d.name}</td>
                <td>
                  {d.provenance === "synthetic_demo" ? <DemoBadge /> : <span className="chip mono">{d.provenance.toUpperCase()}</span>}
                </td>
                <td>
                  <span className="status-tag">{d.lifecycle_state}</span>
                </td>
                <td>{Object.values(d.record_counts).reduce((a, b) => a + b, 0)}</td>
                <td>{d.is_active ? "ACTIVE" : ""}</td>
                <td>
                  <div className="row-actions">
                    <button className="btn btn-sm btn-secondary" onClick={() => inspect(d.id)}>
                      Inspect
                    </button>
                    <button className="btn btn-sm" onClick={() => doSelect(d.id)} disabled={busy || d.is_active}>
                      Select
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {inspectingSummary && schema && (
        <section className="panel">
          <h3 className="section-heading">
            {inspectingSummary.name} — {inspectingSummary.lifecycle_state}
          </h3>

          {Object.entries(schema.files).map(([fileName, info]) => {
            const draft = mappingDraft[fileName] ?? { category: "unknown" as FileCategory, mappings: {} };
            const suggestedMapping = schema.suggested_mappings[fileName];
            return (
              <div key={fileName} className="panel" style={{ marginTop: 12, background: "var(--surface-raised)" }}>
                <div className="field-grid" style={{ marginTop: 0 }}>
                  <div>
                    <div className="field-label">File</div>
                    <div className="field-value mono">{fileName}</div>
                  </div>
                  <div>
                    <div className="field-label">Type / Records</div>
                    <div className="field-value">
                      {info.file_type} / {info.record_count ?? "unknown"}
                    </div>
                  </div>
                  <div>
                    <div className="field-label">Detected Fields</div>
                    <div className="field-value">{info.detected_fields.join(", ") || "—"}</div>
                  </div>
                  <div>
                    <div className="field-label">Suggested Category</div>
                    <div className="field-value">
                      {suggestedMapping.category} (
                      {suggestedMapping.category_confidence === "confirmed" ? "confirmed" : "suggestion — not yet confirmed"})
                    </div>
                  </div>
                </div>

                <div style={{ marginTop: 10 }}>
                  <div className="field-label">Confirm Category</div>
                  <select
                    className="filter-input"
                    style={{ maxWidth: 240 }}
                    value={draft.category}
                    onChange={(e) =>
                      setMappingDraft((prev) => ({ ...prev, [fileName]: { ...draft, category: e.target.value as FileCategory } }))
                    }
                  >
                    {CATEGORY_OPTIONS.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                </div>

                {draft.category !== "unknown" && (
                  <div className="field-grid">
                    {CANONICAL_FIELDS_BY_CATEGORY[draft.category]?.map((canonicalField) => (
                      <div key={canonicalField}>
                        <div className="field-label">{canonicalField}</div>
                        <select
                          className="filter-input"
                          value={draft.mappings[canonicalField] ?? ""}
                          onChange={(e) =>
                            setMappingDraft((prev) => ({
                              ...prev,
                              [fileName]: { ...draft, mappings: { ...draft.mappings, [canonicalField]: e.target.value } },
                            }))
                          }
                        >
                          <option value="">— not mapped —</option>
                          {info.detected_fields.map((f) => (
                            <option key={f} value={f}>
                              {f}
                            </option>
                          ))}
                        </select>
                      </div>
                    ))}
                  </div>
                )}

                <div className="row-actions" style={{ marginTop: 10 }}>
                  <button className="btn btn-sm" onClick={() => doConfirmMapping(fileName)} disabled={busy}>
                    Confirm Mapping
                  </button>
                </div>
              </div>
            );
          })}

          <div className="row-actions" style={{ marginTop: 16 }}>
            <button className="btn btn-sm" onClick={doValidate} disabled={busy}>
              Validate
            </button>
            <button className="btn btn-sm" onClick={doLoad} disabled={busy}>
              Load
            </button>
            <button className="btn btn-sm" onClick={doStartRun} disabled={busy || runStats?.status === "running"}>
              {runStats?.status === "running"
                ? "Processing…"
                : inspectingSummary.lifecycle_state === "processed"
                  ? "Reprocess Dataset"
                  : "Run Processing"}
            </button>
            {activeRunId && (
              <button className="btn btn-sm btn-secondary" onClick={doManualRefresh} disabled={busy}>
                Refresh Now
              </button>
            )}
            {activeRunId && runStats?.status === "completed" && (
              <button className="btn btn-sm btn-secondary" onClick={doEvaluate} disabled={busy}>
                Evaluate
              </button>
            )}
          </div>

          {activeRunId && (
            <ProcessingDashboard
              stats={runStats}
              recentResults={recentResults}
              stale={dashboardStale}
              provenanceLabel={inspectingSummary.provenance === "synthetic_demo" ? "Demo" : "Imported"}
            />
          )}

          {inspectingSummary.validation_report && (
            <div className="panel" style={{ marginTop: 12 }}>
              <h4 className="field-label">Validation Report</h4>
              <div className="summary-cards">
                <div className="summary-card">
                  <span className="kpi-label">Examined</span>
                  <div className="kpi-value" style={{ fontSize: "1.2rem" }}>
                    {inspectingSummary.validation_report.total_records_examined}
                  </div>
                </div>
                <div className="summary-card">
                  <span className="kpi-label">Valid</span>
                  <div className="kpi-value" style={{ fontSize: "1.2rem" }}>
                    {inspectingSummary.validation_report.valid_records}
                  </div>
                </div>
                <div className="summary-card">
                  <span className="kpi-label">Invalid</span>
                  <div className="kpi-value" style={{ fontSize: "1.2rem" }}>
                    {inspectingSummary.validation_report.invalid_records}
                  </div>
                </div>
                <div className="summary-card">
                  <span className="kpi-label">Duplicate IDs</span>
                  <div className="kpi-value" style={{ fontSize: "1.2rem" }}>
                    {inspectingSummary.validation_report.duplicate_identifiers}
                  </div>
                </div>
              </div>
              {inspectingSummary.validation_report.issues.map((issue, i) => (
                <div className="warning-box" key={i} style={{ marginTop: 6 }}>
                  {issue.file_name} row {issue.row_index}: {issue.reason}
                </div>
              ))}
              {inspectingSummary.validation_report.issues_truncated && (
                <p className="section-subtitle">Additional issues were found but not all are shown here.</p>
              )}
            </div>
          )}

          {runHistory.length > 0 && (
            <div className="panel" style={{ marginTop: 12 }}>
              <h4 className="field-label">Processing Run History</h4>
              <p className="section-subtitle">
                Retrieved from the backend's persisted run history — survives a page reload or backend restart.
              </p>
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Run ID</th>
                      <th>Status</th>
                      <th>Started</th>
                      <th>Processed</th>
                      <th>Duplicates</th>
                      <th>Groups</th>
                      <th>Incidents</th>
                    </tr>
                  </thead>
                  <tbody>
                    {runHistory.map((r) => (
                      <tr key={r.run_id}>
                        <td className="mono">{r.run_id}</td>
                        <td>{r.status}</td>
                        <td>{r.started_at}</td>
                        <td>
                          {r.processed_count} / {r.eligible_alert_count}
                        </td>
                        <td>{r.duplicate_count}</td>
                        <td>{r.group_count}</td>
                        <td>{r.incident_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {evaluation && (
            <div className="panel" style={{ marginTop: 12 }}>
              <h4 className="field-label">Evaluation ({String(evaluation.provenance)})</h4>
              <pre className="mono" style={{ whiteSpace: "pre-wrap", fontSize: "0.75rem" }}>
                {JSON.stringify(evaluation, null, 2)}
              </pre>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
