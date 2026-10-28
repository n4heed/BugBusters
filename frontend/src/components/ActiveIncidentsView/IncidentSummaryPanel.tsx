import type { Incident } from "../../types/domain";
import { SeverityBadge } from "../common/SeverityBadge";
import { StatusTag } from "../common/StatusTag";
import { formatTimestamp } from "../../utils/format";

export function IncidentSummaryPanel({
  incident,
  onApproveClick,
  approveDisabled,
  onInvestigateClick,
  investigateDisabled,
  investigating,
}: {
  incident: Incident;
  onApproveClick: () => void;
  approveDisabled: boolean;
  onInvestigateClick?: () => void;
  investigateDisabled?: boolean;
  investigating?: boolean;
}) {
  return (
    <section className="panel">
      <div className="inspector-header">
        <div>
          <span className="incident-id mono">{incident.id}</span>
          <h2 className="inspector-title">{incident.title}</h2>
        </div>
        <SeverityBadge severity={incident.severity} />
      </div>

      <div className="field-grid">
        <div>
          <div className="field-label">Probable Origin</div>
          <div className="field-value">{incident.origin_hypothesis}</div>
        </div>
        <div>
          <div className="field-label">First Seen</div>
          <div className="field-value">{formatTimestamp(incident.first_seen)}</div>
        </div>
        <div>
          <div className="field-label">Last Seen</div>
          <div className="field-value">{formatTimestamp(incident.last_seen)}</div>
        </div>
        <div>
          <div className="field-label">Correlated Alerts</div>
          <div className="field-value">{incident.related_alerts.length}</div>
        </div>
        <div>
          <div className="field-label">Investigation Status</div>
          <div className="field-value">
            <StatusTag status={incident.status} />
          </div>
        </div>
        <div>
          <div className="field-label">Pipeline Status</div>
          <div className="field-value">
            {incident.workflow_status}
            {incident.is_candidate && (
              <span className="chip" style={{ marginLeft: 6 }}>
                Candidate — not yet verified
              </span>
            )}
          </div>
        </div>
      </div>

      <div style={{ marginTop: 10 }}>
        <div className="field-label">Affected Services</div>
        <div className="chip-row">
          {incident.affected_services.map((s) => (
            <span className="chip" key={s}>
              {s}
            </span>
          ))}
        </div>
      </div>

      <div style={{ marginTop: 16, display: "flex", gap: 8 }}>
        <button
          className="btn"
          onClick={onApproveClick}
          disabled={approveDisabled || incident.status === "approved"}
        >
          {incident.status === "approved" ? "Grouping Approved" : "Approve Grouping & Save Verified Pattern"}
        </button>
        {onInvestigateClick && (
          <button className="btn" onClick={onInvestigateClick} disabled={investigateDisabled}>
            {investigating ? "Investigating…" : "Investigate with AI"}
          </button>
        )}
      </div>
    </section>
  );
}
