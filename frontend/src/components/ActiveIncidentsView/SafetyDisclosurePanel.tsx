import type { SafetyDisclosure } from "../../types/domain";

export function SafetyDisclosurePanel({ safety }: { safety: SafetyDisclosure }) {
  const pct = safety.confidence_value != null ? Math.round(safety.confidence_value * 100) : null;

  return (
    <section className="panel">
      <h2 className="section-heading">Safety &amp; Uncertainty Disclosure</h2>

      <div className="field-label">{safety.confidence_label}</div>
      {pct != null ? (
        <div className="confidence-row">
          <div className="confidence-bar-track">
            <div className="confidence-bar-fill" style={{ width: `${pct}%` }} />
          </div>
          <span className="mono">{pct}%</span>
        </div>
      ) : (
        <p className="evidence-block empty" style={{ marginBottom: 10 }}>
          No confidence estimate available for this incident.
        </p>
      )}

      <div className="field-grid">
        <div>
          <div className="field-label">Evidence Completeness</div>
          <div className="field-value">{safety.evidence_completeness}</div>
        </div>
        <div>
          <div className="field-label">Matches Confirmed Pattern</div>
          <div className="field-value">{safety.matches_confirmed_pattern ? "Yes" : "No"}</div>
        </div>
      </div>

      {safety.topology_bias_note && (
        <div className="warning-box">
          <strong>Topology Bias:</strong> {safety.topology_bias_note}
        </div>
      )}
      {safety.weak_support_warning && (
        <div className="warning-box">
          <strong>Weak Support:</strong> {safety.weak_support_warning}
        </div>
      )}
      {safety.missing_evidence_warning && (
        <div className="warning-box critical">
          <strong>Missing Evidence:</strong> {safety.missing_evidence_warning}
        </div>
      )}
    </section>
  );
}
