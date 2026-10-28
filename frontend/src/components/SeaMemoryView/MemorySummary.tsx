import type { SeaMemoryEvent } from "../../types/domain";
import { timeOnly } from "../../utils/format";

export function MemorySummary({ events }: { events: SeaMemoryEvent[] }) {
  const incidentPatterns = events.filter((e) => e.event_type === "INCIDENT_PATTERN_APPROVED").length;
  const tuningApproved = events.filter((e) => e.event_type === "TUNING_RECOMMENDATION_APPROVED").length;
  const latest = events[events.length - 1];

  return (
    <div className="summary-cards">
      <div className="summary-card">
        <span className="kpi-label">Verified Incident Patterns</span>
        <div className="kpi-value">{incidentPatterns}</div>
      </div>
      <div className="summary-card">
        <span className="kpi-label">Approved Tuning Recommendations</span>
        <div className="kpi-value">{tuningApproved}</div>
      </div>
      <div className="summary-card">
        <span className="kpi-label">Recent Review Activity</span>
        <div className="kpi-value">{events.length}</div>
      </div>
      <div className="summary-card">
        <span className="kpi-label">Latest Memory Update</span>
        <div className="kpi-value" style={{ fontSize: "1rem" }}>
          {latest ? timeOnly(latest.timestamp) : "—"}
        </div>
      </div>
    </div>
  );
}
