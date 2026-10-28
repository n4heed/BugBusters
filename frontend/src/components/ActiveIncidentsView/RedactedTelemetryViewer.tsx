import { useState } from "react";
import type { Alert } from "../../types/domain";
import { SeverityBadge } from "../common/SeverityBadge";
import { timeOnly } from "../../utils/format";

export function RedactedTelemetryViewer({ alerts }: { alerts: Alert[] }) {
  const [expanded, setExpanded] = useState(true);

  return (
    <section className="panel">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h2 className="section-heading" style={{ margin: 0 }}>
          Redacted Telemetry
        </h2>
        <button className="btn btn-sm btn-secondary" onClick={() => setExpanded((e) => !e)}>
          {expanded ? "Collapse" : "Expand"}
        </button>
      </div>

      {expanded && (
        <div className="table-wrap" style={{ marginTop: 10 }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Service</th>
                <th>Signal</th>
                <th>Severity</th>
                <th>Host</th>
                <th>IP</th>
                <th>Customer</th>
              </tr>
            </thead>
            <tbody>
              {alerts.map((a) => (
                <tr key={a.id}>
                  <td>{timeOnly(a.timestamp)}</td>
                  <td>{a.service}</td>
                  <td>{a.signal}</td>
                  <td>
                    <SeverityBadge severity={a.severity} />
                  </td>
                  <td>{a.host_redacted}</td>
                  <td>{a.ip_redacted}</td>
                  <td>{a.customer_redacted}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
