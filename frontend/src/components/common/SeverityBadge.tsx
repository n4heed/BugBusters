import type { AlertSeverity } from "../../types/domain";

export function SeverityBadge({ severity }: { severity: AlertSeverity }) {
  return (
    <span className={`severity-badge severity-${severity}`}>
      <span className="severity-dot" aria-hidden="true" />
      {severity}
    </span>
  );
}
