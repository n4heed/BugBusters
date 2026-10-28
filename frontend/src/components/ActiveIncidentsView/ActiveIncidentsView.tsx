import { useEffect, useState } from "react";
import type { Alert, DataSource, Incident, IncidentSummary } from "../../types/domain";
import { api } from "../../api/client";
import {
  demoIncidentSummaries,
  demoIncidents,
  demoSafetyQueueAlerts,
} from "../../data/fixtures";
import { IncidentFeed } from "./IncidentFeed";
import { SafetyQueue } from "./SafetyQueue";
import { IncidentInspector } from "./IncidentInspector";
import { PipelineReplayPanel } from "./PipelineReplayPanel";
import { EvaluationReportPanel } from "./EvaluationReportPanel";
import { ConfirmationDialog } from "../ConfirmationDialog";
import { DemoBadge } from "../common/DemoBadge";
import type { Toast } from "../ToastNotifications";

export function ActiveIncidentsView({
  dataSource,
  onPushToast,
  onIncidentApproved,
}: {
  dataSource: DataSource;
  onPushToast: (toast: Omit<Toast, "id">) => void;
  onIncidentApproved: (incidentId: string, title: string) => void;
}) {
  const [summaries, setSummaries] = useState<IncidentSummary[]>([]);
  const [incidentCache, setIncidentCache] = useState<Record<string, Incident>>({});
  const [safetyAlerts, setSafetyAlerts] = useState<Alert[]>([]);
  const [reviewedIds, setReviewedIds] = useState<Set<string>>(new Set());
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [usingDemoFallback, setUsingDemoFallback] = useState(false);
  const [approveTarget, setApproveTarget] = useState<Incident | null>(null);
  const [approving, setApproving] = useState(false);
  const [investigating, setInvestigating] = useState(false);

  useEffect(() => {
    let active = true;
    async function load() {
      setLoading(true);
      setErrorMsg(null);
      if (dataSource === "live") {
        try {
          const [list, safety] = await Promise.all([api.listIncidents(), api.listSafetyQueue()]);
          if (!active) return;
          setSummaries(list);
          setSafetyAlerts(safety);
          setUsingDemoFallback(false);
          setSelectedId(list[0]?.id ?? null);
          setLoading(false);
          return;
        } catch {
          if (!active) return;
          setUsingDemoFallback(true);
        }
      }
      setSummaries(demoIncidentSummaries);
      setSafetyAlerts(demoSafetyQueueAlerts);
      setIncidentCache(Object.fromEntries(demoIncidents.map((i) => [i.id, i])));
      setSelectedId(demoIncidentSummaries[0]?.id ?? null);
      setLoading(false);
    }
    load();
    return () => {
      active = false;
    };
  }, [dataSource]);

  useEffect(() => {
    if (!selectedId || incidentCache[selectedId]) return;
    let active = true;
    if (dataSource === "live" && !usingDemoFallback) {
      api
        .getIncident(selectedId)
        .then((incident) => {
          if (active) setIncidentCache((prev) => ({ ...prev, [selectedId]: incident }));
        })
        .catch(() => {
          const fallback = demoIncidents.find((i) => i.id === selectedId);
          if (active && fallback) setIncidentCache((prev) => ({ ...prev, [selectedId]: fallback }));
        });
    }
    return () => {
      active = false;
    };
  }, [selectedId, dataSource, usingDemoFallback, incidentCache]);

  const selectedIncident = selectedId ? incidentCache[selectedId] : undefined;
  const isDemo = dataSource === "demo" || usingDemoFallback;

  async function confirmApprove() {
    if (!approveTarget) return;
    setApproving(true);
    try {
      let result;
      if (dataSource === "live" && !usingDemoFallback) {
        result = await api.approveIncident(approveTarget.id);
      } else {
        result = {
          success: true,
          demo_only: true,
          message: "APPROVED IN DEMO — grouping saved as a verified pattern. Not applied to production systems.",
        };
      }
      if (result.success) {
        setIncidentCache((prev) => ({
          ...prev,
          [approveTarget.id]: { ...approveTarget, status: "approved" },
        }));
        setSummaries((prev) =>
          prev.map((s) => (s.id === approveTarget.id ? { ...s, status: "approved" } : s)),
        );
        onIncidentApproved(approveTarget.id, approveTarget.title);
        onPushToast({
          kind: "success",
          message: result.demo_only
            ? "APPROVED IN DEMO — grouping saved as a verified pattern (not applied to production)."
            : "Grouping approved and verified pattern saved.",
        });
      } else {
        onPushToast({ kind: "error", message: result.message });
      }
    } catch {
      onPushToast({ kind: "error", message: "Approval failed — the incident's previous state was preserved." });
    } finally {
      setApproving(false);
      setApproveTarget(null);
    }
  }

  async function handleInvestigate() {
    if (!selectedIncident) return;
    setInvestigating(true);
    try {
      const result = await api.investigateIncident(selectedIncident.id);
      setIncidentCache((prev) => ({ ...prev, [selectedIncident.id]: result.incident }));
      if (result.investigation.evidence_source === "llm") {
        onPushToast({
          kind: "success",
          message: `AI-assisted investigation complete (${result.incident.ai_model_used ?? "model"}).`,
        });
      } else {
        onPushToast({
          kind: "info",
          message: `Investigation complete — deterministic result (${result.incident.ai_status}). See AI status for why.`,
        });
      }
    } catch {
      onPushToast({
        kind: "error",
        message: "AI-assisted investigation failed — the incident and its alerts were preserved unchanged.",
      });
    } finally {
      setInvestigating(false);
    }
  }

  if (loading) return <p className="loading-text">Loading incidents…</p>;

  return (
    <div>
      <PipelineReplayPanel dataSource={dataSource} />
      <EvaluationReportPanel dataSource={dataSource} />
      {isDemo && (
        <div style={{ marginBottom: 12 }}>
          <DemoBadge /> <span className="section-subtitle" style={{ display: "inline" }}>
            Showing synthetic fixture incidents — backend unreachable or demo mode selected.
          </span>
        </div>
      )}
      {errorMsg && <p className="error-text">{errorMsg}</p>}

      <div className="incidents-layout">
        <div>
          <IncidentFeed incidents={summaries} selectedId={selectedId} onSelect={setSelectedId} />
          <SafetyQueue
            alerts={safetyAlerts}
            reviewedIds={reviewedIds}
            onMarkReviewed={(id) => setReviewedIds((prev) => new Set(prev).add(id))}
          />
        </div>

        <div>
          {selectedIncident ? (
            <IncidentInspector
              incident={selectedIncident}
              approveDisabled={approving}
              onApproveClick={() => setApproveTarget(selectedIncident)}
              onInvestigateClick={handleInvestigate}
              investigateDisabled={investigating || dataSource !== "live" || usingDemoFallback}
              investigating={investigating}
            />
          ) : (
            <p className="loading-text">Loading incident detail…</p>
          )}
        </div>
      </div>

      {approveTarget && (
        <ConfirmationDialog
          title="Approve Grouping & Save Verified Pattern"
          confirmLabel="Approve"
          pending={approving}
          onConfirm={confirmApprove}
          onCancel={() => setApproveTarget(null)}
        >
          <p>
            You are approving <strong>{approveTarget.id}</strong> — {approveTarget.title}.
          </p>
          <p>
            This records that a human reviewed and accepted this alert grouping as a verified incident pattern. It
            does not close, mute, or fix any alert, and does not change production systems. A corresponding entry
            will be added to SEA Memory.
          </p>
        </ConfirmationDialog>
      )}
    </div>
  );
}
