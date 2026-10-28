import type { Incident } from "../../types/domain";
import { IncidentSummaryPanel } from "./IncidentSummaryPanel";
import { DependencyGraph } from "./DependencyGraph";
import { InvestigationExplanation } from "./InvestigationExplanation";
import { SafetyDisclosurePanel } from "./SafetyDisclosurePanel";
import { RedactedTelemetryViewer } from "./RedactedTelemetryViewer";

export function IncidentInspector({
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
    <div>
      <IncidentSummaryPanel
        incident={incident}
        onApproveClick={onApproveClick}
        approveDisabled={approveDisabled}
        onInvestigateClick={onInvestigateClick}
        investigateDisabled={investigateDisabled}
        investigating={investigating}
      />

      <section className="panel">
        <h2 className="section-heading">Service Dependency Graph</h2>
        <DependencyGraph
          services={incident.affected_services}
          dependencies={incident.dependencies}
          origin={incident.origin_hypothesis}
        />
      </section>

      <InvestigationExplanation evidence={incident.evidence} incident={incident} />
      <SafetyDisclosurePanel safety={incident.safety} />
      <RedactedTelemetryViewer alerts={incident.related_alerts} />
    </div>
  );
}
