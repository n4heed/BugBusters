import type { SeaMemoryEvent } from "../../types/domain";
import { formatTimestamp } from "../../utils/format";

const EVENT_LABELS: Record<SeaMemoryEvent["event_type"], string> = {
  INCIDENT_PATTERN_APPROVED: "INCIDENT_PATTERN_APPROVED",
  TUNING_RECOMMENDATION_APPROVED: "TUNING_RECOMMENDATION_APPROVED",
  TUNING_RECOMMENDATION_REJECTED: "TUNING_RECOMMENDATION_REJECTED",
};

export function MemoryEventLog({ events }: { events: SeaMemoryEvent[] }) {
  if (events.length === 0) {
    return (
      <p className="empty-state">
        No memory entries yet. Approve an incident grouping or a tuning recommendation to create one.
      </p>
    );
  }

  const sorted = [...events].sort((a, b) => b.timestamp.localeCompare(a.timestamp));

  return (
    <div className="memory-log">
      {sorted.map((e) => (
        <div className="memory-entry" key={e.id}>
          <div className="memory-entry-head">
            <span>[{formatTimestamp(e.timestamp)}] {EVENT_LABELS[e.event_type]}</span>
            <span>{e.status}</span>
          </div>
          <div>ID: {e.entity_id}</div>
          <div>Decision: {e.decision}</div>
          {e.pattern && <div>Pattern: {e.pattern}</div>}
        </div>
      ))}
    </div>
  );
}
