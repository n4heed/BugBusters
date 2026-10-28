import { useState } from "react";
import type { AIStatus, AIUsageSummary, DataSource, ReplayResponse } from "../../types/domain";
import { api } from "../../api/client";

export function PipelineReplayPanel({ dataSource }: { dataSource: DataSource }) {
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<ReplayResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [aiStatus, setAiStatus] = useState<AIStatus | null>(null);
  const [aiUsage, setAiUsage] = useState<AIUsageSummary | null>(null);

  if (dataSource !== "live") {
    return (
      <section className="panel" style={{ marginBottom: 16 }}>
        <h2 className="section-heading">Agent Pipeline // Deterministic Replay</h2>
        <p className="section-subtitle">
          Runs 6 fixed synthetic alerts through the real Triage → Investigation → Verification pipeline
          and scores the result against known ground truth.
        </p>
        <p className="loading-text">
          Requires a reachable backend — currently showing demo fixtures only. Start the FastAPI backend to run this.
        </p>
      </section>
    );
  }

  async function run() {
    setRunning(true);
    setError(null);
    try {
      const r = await api.runPipelineReplay();
      setResult(r);
      const [status, usage] = await Promise.all([api.getAIStatus(), api.getAIUsage()]);
      setAiStatus(status);
      setAiUsage(usage.summary);
    } catch {
      setError("Replay request failed — the backend may not have the pipeline endpoints available yet.");
    } finally {
      setRunning(false);
    }
  }

  return (
    <section className="panel" style={{ marginBottom: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
        <div>
          <h2 className="section-heading" style={{ margin: 0 }}>
            Agent Pipeline // Deterministic Replay
          </h2>
          <p className="section-subtitle" style={{ marginTop: 4 }}>
            6 fixed alerts, known expected outcomes — the main correctness demonstration for the pipeline.
          </p>
        </div>
        <button className="btn btn-sm" onClick={run} disabled={running}>
          {running ? "Running…" : "Run Replay"}
        </button>
      </div>

      {error && <p className="error-text">{error}</p>}

      {result && (
        <>
          <div className="table-wrap" style={{ marginTop: 12 }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Alert</th>
                  <th>Workflow Status</th>
                  <th>Incident</th>
                  <th>Processing (ms)</th>
                  <th>Error</th>
                </tr>
              </thead>
              <tbody>
                {result.results.map((r) => (
                  <tr key={r.alert_id}>
                    <td>{r.alert_id}</td>
                    <td>{r.workflow_status}</td>
                    <td>{r.incident_id ?? "—"}</td>
                    <td>{r.processing_ms ?? "—"}</td>
                    <td>{r.error ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="summary-cards" style={{ marginTop: 12 }}>
            <div className="summary-card">
              <span className="kpi-label">Alert-Pair Precision</span>
              <div className="kpi-value" style={{ fontSize: "1.2rem" }}>
                {result.evaluation.alert_pair_precision_pct ?? "—"}%
              </div>
            </div>
            <div className="summary-card">
              <span className="kpi-label">Alert-Pair Recall</span>
              <div className="kpi-value" style={{ fontSize: "1.2rem" }}>
                {result.evaluation.alert_pair_recall_pct ?? "—"}%
              </div>
            </div>
            <div className="summary-card">
              <span className="kpi-label">Verification Failure Rate</span>
              <div className="kpi-value" style={{ fontSize: "1.2rem" }}>
                {result.evaluation.verification_failure_rate_pct ?? "—"}%
              </div>
            </div>
            <div className="summary-card">
              <span className="kpi-label">Avg Processing Latency</span>
              <div className="kpi-value" style={{ fontSize: "1.2rem" }}>
                {result.evaluation.avg_processing_latency_ms ?? "—"} ms
              </div>
            </div>
          </div>
          <p className="section-subtitle" style={{ marginTop: 8 }}>
            Evaluated against a 6-alert synthetic ground-truth scenario ({result.evaluation.evaluated_on}) — not a
            benchmark against a real dataset.
          </p>
        </>
      )}

      {aiStatus && (
        <div className="panel" style={{ marginTop: 12, background: "var(--surface-raised)" }}>
          <h3 className="section-heading" style={{ fontSize: "0.7rem" }}>
            AI Integration
          </h3>
          {!aiStatus.ai_enabled ? (
            <p className="loading-text">
              AI DISABLED — no model provider configured ({'LLM_ENABLED=false'}). Every investigation above used the
              deterministic pipeline only.
            </p>
          ) : (
            <div className="summary-cards">
              <div className="summary-card">
                <span className="kpi-label">Provider / Model</span>
                <div className="kpi-value" style={{ fontSize: "1rem" }}>
                  {aiStatus.provider} / {aiStatus.model}
                </div>
              </div>
              <div className="summary-card">
                <span className="kpi-label">Call Budget Used</span>
                <div className="kpi-value" style={{ fontSize: "1.2rem" }}>
                  {aiStatus.budget_used} / {aiStatus.max_calls_per_run}
                </div>
              </div>
              <div className="summary-card">
                <span className="kpi-label">Tokens (in/out)</span>
                <div className="kpi-value" style={{ fontSize: "1rem" }}>
                  {aiUsage?.total_input_tokens ?? "—"} / {aiUsage?.total_output_tokens ?? "—"}
                  {aiUsage?.tokens_are_estimated ? " (est.)" : ""}
                </div>
              </div>
              <div className="summary-card">
                <span className="kpi-label">Avg Latency</span>
                <div className="kpi-value" style={{ fontSize: "1.2rem" }}>
                  {aiUsage?.avg_latency_ms ?? "—"} ms
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
