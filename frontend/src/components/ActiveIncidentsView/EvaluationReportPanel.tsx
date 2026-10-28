import { useState } from "react";
import type { DataSource, EvaluationReport } from "../../types/domain";
import { api } from "../../api/client";

function ProvenanceTag({ label }: { label: string }) {
  const tone = label === "NOT MEASURED" ? "var(--text-muted)" : "var(--healthy)";
  return (
    <span className="chip mono" style={{ color: tone, borderColor: tone }}>
      {label}
    </span>
  );
}

export function EvaluationReportPanel({ dataSource }: { dataSource: DataSource }) {
  const [report, setReport] = useState<EvaluationReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (dataSource !== "live") {
    return (
      <section className="panel" style={{ marginBottom: 16 }}>
        <h2 className="section-heading">Evaluation &amp; Reliability Report</h2>
        <p className="loading-text">Requires a reachable backend to compute — unavailable in demo mode.</p>
      </section>
    );
  }

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setReport(await api.getEvaluationReport());
    } catch {
      setError("Could not load the evaluation report from the backend.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="panel" style={{ marginBottom: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
        <div>
          <h2 className="section-heading" style={{ margin: 0 }}>
            Evaluation &amp; Reliability Report
          </h2>
          <p className="section-subtitle" style={{ marginTop: 4 }}>
            Synthetic smoke tests, 4 baselines on identical input, and per-case error analysis.
          </p>
        </div>
        <button className="btn btn-sm" onClick={load} disabled={loading}>
          {loading ? "Running…" : "Run Evaluation"}
        </button>
      </div>

      {error && <p className="error-text">{error}</p>}

      {report && (
        <>
          <div className="chip-row" style={{ marginTop: 10 }}>
            <span>
              Mode A — Smoke Tests: {report.mode_a_synthetic_smoke_tests.passed}/
              {report.mode_a_synthetic_smoke_tests.total_checks}{" "}
              <ProvenanceTag label={report.mode_a_synthetic_smoke_tests.provenance} />
            </span>
            <span>
              Mode B — Challenge Dataset: <ProvenanceTag label={report.mode_b_challenge_dataset_benchmark.provenance} />
            </span>
            <span>
              Mode C — Selective AI Eval: <ProvenanceTag label={report.mode_c_selective_ai_evaluation.provenance} />
            </span>
          </div>

          <div className="table-wrap" style={{ marginTop: 12 }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Baseline</th>
                  <th>Notifications</th>
                  <th>Precision</th>
                  <th>Recall</th>
                  <th>AI Calls</th>
                  <th>Latency (ms)</th>
                </tr>
              </thead>
              <tbody>
                {report.baselines.results.map((b) => (
                  <tr key={b.name}>
                    <td>{b.name}</td>
                    <td>{b.notification_count}</td>
                    <td>{b.precision_pct ?? "N/A"}</td>
                    <td>{b.recall_pct ?? "N/A"}</td>
                    <td>{b.ai_calls_made}</td>
                    <td>{b.total_latency_ms}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="section-subtitle" style={{ marginTop: 6 }}>
            {report.baselines.dataset} — <ProvenanceTag label={report.baselines.provenance} />
          </p>

          <div style={{ marginTop: 12 }}>
            <div className="field-label">
              Error Analysis ({report.error_analysis.total_findings} finding
              {report.error_analysis.total_findings === 1 ? "" : "s"})
            </div>
            {report.error_analysis.findings.length === 0 ? (
              <p className="empty-state">No findings against documented expectations.</p>
            ) : (
              report.error_analysis.findings.map((f) => (
                <div className="warning-box" key={f.case_ref} style={{ marginTop: 8 }}>
                  <strong>
                    [{f.case_ref}] {f.category}
                  </strong>
                  <div>Observed: {f.observed}</div>
                  <div>Expected: {f.expected}</div>
                </div>
              ))
            )}
          </div>
        </>
      )}
    </section>
  );
}
