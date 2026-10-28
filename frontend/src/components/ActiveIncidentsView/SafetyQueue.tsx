import { useState } from "react";
import type { Alert } from "../../types/domain";
import { SeverityBadge } from "../common/SeverityBadge";
import { timeOnly } from "../../utils/format";

export function SafetyQueue({
  alerts,
  reviewedIds,
  onMarkReviewed,
}: {
  alerts: Alert[];
  reviewedIds: Set<string>;
  onMarkReviewed: (id: string) => void;
}) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  return (
    <section className="panel" aria-labelledby="safety-queue-heading">
      <div className="safety-queue-heading">
        <h2 className="section-heading" id="safety-queue-heading" style={{ margin: 0 }}>
          Safety Queue // Unclustered Alerts
        </h2>
      </div>
      <p className="section-subtitle">
        An alert without a known historical incident may still represent a real failure. Nothing here is discarded.
      </p>

      {alerts.length === 0 ? (
        <p className="empty-state">No unclustered alerts awaiting investigation.</p>
      ) : (
        alerts.map((alert) => {
          const expanded = expandedId === alert.id;
          const reviewed = reviewedIds.has(alert.id);
          return (
            <div key={alert.id} className="safety-alert-card">
              <div className="safety-alert-top">
                <span className="safety-alert-service mono">{alert.service}</span>
                <SeverityBadge severity={alert.severity} />
              </div>
              <p className="safety-alert-signal">{alert.signal}</p>
              <p className="safety-alert-reason">
                Status: {reviewed ? "Reviewed" : "Awaiting Investigation"}
                <br />
                Reason: {alert.safety_reason ?? "No sufficiently supported grouping found."}
              </p>

              {expanded && (
                <div className="field-grid" style={{ marginTop: 10 }}>
                  <div>
                    <div className="field-label">Timestamp</div>
                    <div className="field-value">{timeOnly(alert.timestamp)}</div>
                  </div>
                  <div>
                    <div className="field-label">Host</div>
                    <div className="field-value">{alert.host_redacted}</div>
                  </div>
                  <div>
                    <div className="field-label">IP</div>
                    <div className="field-value">{alert.ip_redacted}</div>
                  </div>
                  <div>
                    <div className="field-label">Customer</div>
                    <div className="field-value">{alert.customer_redacted}</div>
                  </div>
                </div>
              )}

              <div className="row-actions" style={{ marginTop: 10 }}>
                <button
                  className="btn btn-sm btn-secondary"
                  onClick={() => setExpandedId(expanded ? null : alert.id)}
                >
                  {expanded ? "Hide Telemetry" : "Inspect Telemetry"}
                </button>
                {!reviewed && (
                  <button className="btn btn-sm" onClick={() => onMarkReviewed(alert.id)}>
                    Mark Reviewed
                  </button>
                )}
              </div>
            </div>
          );
        })
      )}
    </section>
  );
}
