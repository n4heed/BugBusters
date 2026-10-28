import type { Evidence, Incident } from "../../types/domain";

function EvidenceList({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="evidence-block">
      <h4>{title}</h4>
      {items.length === 0 ? (
        <p className="empty">None recorded</p>
      ) : (
        <ul>
          {items.map((item, i) => (
            <li key={i}>{item}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

const AI_STATUS_LABEL: Record<Incident["ai_status"], string> = {
  AI_NOT_RUN: "AI NOT RUN",
  AI_DISABLED: "AI DISABLED",
  AI_CALLED: "AI-ASSISTED",
  AI_BUDGET_EXHAUSTED: "AI BUDGET EXHAUSTED",
  AI_CALL_FAILED: "AI CALL FAILED — FELL BACK",
};

function AIStatusBlock({ incident }: { incident: Incident }) {
  return (
    <div className="evidence-block" style={{ gridColumn: "1 / -1" }}>
      <h4>AI-Assisted Analysis</h4>
      <div className="field-grid" style={{ marginTop: 0 }}>
        <div>
          <div className="field-label">Status</div>
          <div className="field-value">{AI_STATUS_LABEL[incident.ai_status]}</div>
        </div>
        {incident.ai_status === "AI_CALLED" && (
          <>
            <div>
              <div className="field-label">Model</div>
              <div className="field-value">{incident.ai_model_used ?? "—"}</div>
            </div>
            <div>
              <div className="field-label">Alerts Considered</div>
              <div className="field-value">{incident.ai_alerts_considered}</div>
            </div>
            <div>
              <div className="field-label">Historical Cases Considered</div>
              <div className="field-value">{incident.ai_historical_cases_considered}</div>
            </div>
          </>
        )}
      </div>
      <p className="evidence-block empty" style={{ marginTop: 8 }}>
        {incident.ai_reason}
      </p>
    </div>
  );
}

export function InvestigationExplanation({ evidence, incident }: { evidence: Evidence; incident: Incident }) {
  return (
    <section className="panel">
      <h2 className="section-heading">Investigation Agent // Evidence &amp; Verification</h2>
      <p className="evidence-narrative mono">{evidence.narrative}</p>
      <div className="evidence-grid">
        <EvidenceList title="Overlapping Time Windows" items={evidence.overlapping_time_windows} />
        <EvidenceList title="Supporting Dependencies" items={evidence.supporting_dependencies} />
        <EvidenceList title="Supporting Metrics / Logs" items={evidence.supporting_metrics} />
        <EvidenceList title="Missing or Contradictory Evidence" items={evidence.missing_or_contradictory} />
        <EvidenceList title="Similar Historical Incidents" items={evidence.similar_historical_incidents} />
        <EvidenceList title="Unresolved Uncertainty" items={evidence.unresolved_uncertainty} />
        <AIStatusBlock incident={incident} />
      </div>
    </section>
  );
}
