import type { IncidentSummary } from "../../types/domain";
import { SeverityBadge } from "../common/SeverityBadge";
import { StatusTag } from "../common/StatusTag";
import { timeOnly } from "../../utils/format";

export function IncidentFeed({
  incidents,
  selectedId,
  onSelect,
}: {
  incidents: IncidentSummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  if (incidents.length === 0) {
    return <p className="empty-state">No correlated incidents in the current window.</p>;
  }

  return (
    <div className="incident-feed-list" role="list">
      {incidents.map((incident) => (
        <button
          key={incident.id}
          role="listitem"
          className={`incident-card${selectedId === incident.id ? " selected" : ""}`}
          aria-pressed={selectedId === incident.id}
          onClick={() => onSelect(incident.id)}
        >
          <div className="incident-card-top">
            <span className="incident-id mono">{incident.id}</span>
            <SeverityBadge severity={incident.severity} />
          </div>
          <p className="incident-title">{incident.title}</p>
          <div className="incident-meta-row">
            <span>{incident.related_alert_count} related alerts</span>
            <span>Origin: {incident.origin_hypothesis}</span>
            <span>{incident.affected_service_count} services</span>
          </div>
          <div className="incident-meta-row">
            <StatusTag status={incident.status} />
            <span>Last activity {timeOnly(incident.last_activity)}</span>
            {incident.is_candidate && <span className="chip">Candidate</span>}
          </div>
        </button>
      ))}
    </div>
  );
}
