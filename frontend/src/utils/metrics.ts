// Shared metric definitions. No official notification-reduction definition
// exists for this challenge, so the definition used here is explicit:
// each correlated incident card plus each independently surfaced safety
// alert counts as one actionable notification, measured against raw alert
// count. Tuning recommendations are review summaries, not paging
// notifications, so they are excluded from the notification count.
// Incident recall and grouping precision need labelled ground truth this
// demo does not have, so they are reported as "not measured".
import type { EvaluationMetrics, Incident, IncidentSummary } from "../types/domain";

export function computeDemoMetrics(
  incidents: IncidentSummary[] | Incident[],
  safetyAlertCount: number,
  rawAlertCount: number,
): EvaluationMetrics {
  const correlated = incidents.length;
  const notifications = correlated + safetyAlertCount;
  const reduction = rawAlertCount > 0 ? Math.round((1 - notifications / rawAlertCount) * 1000) / 10 : null;

  return {
    raw_alerts_24h: rawAlertCount,
    correlated_incidents: correlated,
    notification_reduction_pct: reduction,
    incident_recall_pct: null,
    grouping_precision_pct: null,
    is_demo_data: true,
  };
}
