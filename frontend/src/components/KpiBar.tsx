import type { EvaluationMetrics } from "../types/domain";

function KpiCard({
  label,
  value,
  desc,
  title,
}: {
  label: string;
  value: string;
  desc: string;
  title?: string;
}) {
  return (
    <div className="kpi-card">
      <span className="kpi-label">{label}</span>
      <span className={`kpi-value${value === "—" ? " dim" : ""}`} title={title}>
        {value}
      </span>
      <span className="kpi-desc">{desc}</span>
    </div>
  );
}

export function KpiBar({ metrics }: { metrics: EvaluationMetrics | null }) {
  const m = metrics;
  const fmtInt = (v: number | null | undefined) => (v === null || v === undefined ? "—" : String(v));
  const fmtPct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${v}%`);

  return (
    <div className="kpi-bar">
      <KpiCard label="Raw Alerts (24H)" value={fmtInt(m?.raw_alerts_24h)} desc="Total ingested alert events" />
      <KpiCard
        label="Correlated Incidents"
        value={fmtInt(m?.correlated_incidents)}
        desc="Distinct incident groupings proposed"
      />
      <KpiCard
        label="Notification Reduction"
        value={fmtPct(m?.notification_reduction_pct)}
        desc="vs. raw alert count"
        title="Definition: 1 − (correlated incidents + standalone safety alerts) / raw alerts. Tuning recommendations are review summaries, not notifications."
      />
      <KpiCard
        label="Incident Recall"
        value={m?.incident_recall_pct == null ? "—" : fmtPct(m.incident_recall_pct)}
        desc={m?.incident_recall_pct == null ? "Not measured" : "vs. labelled ground truth"}
      />
      <KpiCard
        label="Grouping Precision"
        value={m?.grouping_precision_pct == null ? "—" : fmtPct(m.grouping_precision_pct)}
        desc={m?.grouping_precision_pct == null ? "Not measured" : "vs. labelled ground truth"}
      />
    </div>
  );
}
