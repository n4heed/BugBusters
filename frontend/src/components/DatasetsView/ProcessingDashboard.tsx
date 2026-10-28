import type { ProcessingResult, RunStatsSummary } from "../../types/domain";

/** Phase 4: a live, data-driven view of ONE dataset's processing run.
 * Every number here comes straight from `stats` / `recentResults` — both
 * fetched directly from the backend's persisted processing_results /
 * processing_groups rows (see api.getRunSummary / api.getRunRecentResults)
 * — never a locally-maintained counter that could drift from the database.
 */
export function ProcessingDashboard({
  stats,
  recentResults,
  stale,
  provenanceLabel,
}: {
  stats: RunStatsSummary | null;
  recentResults: ProcessingResult[];
  stale: boolean;
  provenanceLabel: string;
}) {
  if (!stats) {
    return (
      <div className="panel" style={{ marginTop: 12 }}>
        <h4 className="field-label">Processing Dashboard</h4>
        <p className="loading-text">Loading run status from the backend…</p>
      </div>
    );
  }

  return (
    <div className="panel" style={{ marginTop: 12 }}>
      <h4 className="field-label">
        Processing Dashboard — run <span className="mono">{stats.run_id}</span>
      </h4>

      {stale && (
        <div className="processing-dashboard-stale">
          Could not reach the backend for the latest update — showing the last known counts.
        </div>
      )}

      <div className="chip-row" style={{ marginBottom: 10 }}>
        <span className={`status-tag ${stats.status}`}>{stats.status}</span>
        <span className="chip mono">
          processed {stats.processed_count} / {stats.eligible_alert_count} eligible
        </span>
        <span className="chip mono">last update: {stats.last_progress_at ?? "—"}</span>
        {stats.error_summary && <span className="chip mono">error: {stats.error_summary}</span>}
      </div>

      <div className="summary-cards">
        <div className="summary-card">
          <span className="kpi-label">{provenanceLabel} · Total Eligible</span>
          <div className="kpi-value" style={{ fontSize: "1.2rem" }}>
            {stats.eligible_alert_count}
          </div>
        </div>
        <div className="summary-card">
          <span className="kpi-label">{provenanceLabel} · Processed</span>
          <div className="kpi-value" style={{ fontSize: "1.2rem" }}>
            {stats.processed_count}
          </div>
        </div>
        <div className="summary-card">
          <span className="kpi-label">{provenanceLabel} · Unique</span>
          <div className="kpi-value" style={{ fontSize: "1.2rem" }}>
            {stats.unique_count}
          </div>
          <div className="kpi-desc">
            {stats.grouped_alert_count} related · {stats.standalone_count} standalone
          </div>
        </div>
        <div className="summary-card">
          <span className="kpi-label">{provenanceLabel} · Redundant</span>
          <div className="kpi-value" style={{ fontSize: "1.2rem" }}>
            {stats.duplicate_count}
          </div>
        </div>
        <div className="summary-card">
          <span className="kpi-label">{provenanceLabel} · Uncertain</span>
          <div className="kpi-value" style={{ fontSize: "1.2rem" }}>
            {stats.uncertain_count}
          </div>
        </div>
        <div className="summary-card">
          <span className="kpi-label">{provenanceLabel} · Groups</span>
          <div className="kpi-value" style={{ fontSize: "1.2rem" }}>
            {stats.group_count}
          </div>
          <div className="kpi-desc">{stats.incident_count} incident record(s)</div>
        </div>
      </div>

      <h4 className="field-label" style={{ marginTop: 14 }}>
        Recent Activity
      </h4>
      <p className="section-subtitle">
        The {recentResults.length} most recently processed alert(s) for this run — not a sample, the actual
        persisted results.
      </p>
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Alert ID</th>
              <th>Disposition</th>
              <th>Workflow Status</th>
              <th>Duplicate Of</th>
              <th>Group</th>
            </tr>
          </thead>
          <tbody>
            {recentResults.map((r) => (
              <tr key={r.alert_id}>
                <td className="mono">{r.alert_id}</td>
                <td>
                  {r.disposition ? (
                    <span className={`status-tag ${r.disposition}`}>{r.disposition}</span>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="mono">{r.workflow_status}</td>
                <td className="mono">{r.duplicate_of ?? "—"}</td>
                <td className="mono">{r.incident_id ?? "—"}</td>
              </tr>
            ))}
            {recentResults.length === 0 && (
              <tr>
                <td colSpan={5} className="section-subtitle">
                  No results persisted yet for this run.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
