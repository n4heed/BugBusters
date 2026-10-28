import { useEffect, useState } from "react";
import { GlobalHeader } from "./components/GlobalHeader";
import { KpiBar } from "./components/KpiBar";
import { MainTabs, type TabKey } from "./components/MainTabs";
import { ActiveIncidentsView } from "./components/ActiveIncidentsView/ActiveIncidentsView";
import { NoiseTuningView } from "./components/NoiseTuningView/NoiseTuningView";
import { SeaMemoryView } from "./components/SeaMemoryView/SeaMemoryView";
import { DatasetsView } from "./components/DatasetsView/DatasetsView";
import { ToastNotifications, type Toast } from "./components/ToastNotifications";
import { useAlertStream } from "./api/useAlertStream";
import { api } from "./api/client";
import { demoIncidentSummaries, demoRawAlertCount, demoSafetyQueueAlerts } from "./data/fixtures";
import { computeDemoMetrics } from "./utils/metrics";
import type { BackendStatus, DataSource, EvaluationMetrics, SeaMemoryEvent } from "./types/domain";

export default function App() {
  const [activeTab, setActiveTab] = useState<TabKey>("incidents");
  // dataSource drives which adapter each view fetches from (real REST calls
  // vs. the local fixtures.ts fallback). backendStatus is a SEPARATE,
  // narrower claim shown in the header: only "can this process be reached",
  // never "is this production data" — see GlobalHeader's DATA: SYNTHETIC
  // DEMO pill, which stays shown regardless, because this prototype has no
  // production alert source at all.
  const [dataSource, setDataSource] = useState<DataSource>("demo");
  const [backendStatus, setBackendStatus] = useState<BackendStatus>("UNKNOWN");
  const [metrics, setMetrics] = useState<EvaluationMetrics | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [demoMemoryLog, setDemoMemoryLog] = useState<SeaMemoryEvent[]>([]);
  // Bumped whenever the active dataset changes, so views keyed on it remount
  // and refetch rather than showing stale data from the previous dataset.
  const [datasetVersion, setDatasetVersion] = useState(0);

  const stream = useAlertStream();

  useEffect(() => {
    let active = true;
    api
      .health()
      .then(() => {
        if (active) {
          setDataSource("live");
          setBackendStatus("REACHABLE");
        }
      })
      .catch(() => {
        if (active) {
          setDataSource("demo");
          setBackendStatus("UNREACHABLE");
        }
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    async function loadMetrics() {
      if (dataSource === "live") {
        try {
          const m = await api.getMetrics();
          if (active) setMetrics(m);
          return;
        } catch {
          // fall through to demo metrics below
        }
      }
      if (active) {
        setMetrics(computeDemoMetrics(demoIncidentSummaries, demoSafetyQueueAlerts.length, demoRawAlertCount));
      }
    }
    loadMetrics();
    return () => {
      active = false;
    };
  }, [dataSource, datasetVersion]);

  function pushToast(toast: Omit<Toast, "id">) {
    setToasts((prev) => [...prev, { ...toast, id: `${Date.now()}-${Math.random()}` }]);
  }

  function dismissToast(id: string) {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }

  function recordDemoMemoryEvent(event: Omit<SeaMemoryEvent, "id" | "timestamp" | "status">) {
    setDemoMemoryLog((prev) => [
      ...prev,
      {
        ...event,
        id: `${Date.now()}-${Math.random()}`,
        timestamp: new Date().toISOString(),
        status: "DEMO MEMORY ENTRY",
      },
    ]);
  }

  return (
    <div className="app-shell">
      <GlobalHeader
        backendStatus={backendStatus}
        streamStatus={stream.status}
        simulationRunning={stream.simulationRunning}
        onStartStream={stream.connect}
        onStopStream={stream.disconnect}
        onStartSimulation={stream.startSimulation}
        onStopSimulation={stream.stopSimulation}
      />
      <KpiBar metrics={metrics} />
      <MainTabs active={activeTab} onChange={setActiveTab} />

      <main className="tab-content">
        {stream.events.length > 0 && (
          <div className="panel" style={{ marginBottom: 16 }}>
            <h2 className="section-heading">Live Ingestion Feed</h2>
            <p className="section-subtitle">
              Synthetic events from the demo simulation, run through the real Triage → Investigation →
              Verification pipeline. These are not production telemetry.
            </p>
            <div className="stream-events-list">
              {stream.events.map((e) => (
                <div className="stream-event-row" key={e.id}>
                  <span>{e.timestamp.slice(11, 19)}Z</span>
                  <span>{e.service}</span>
                  <span>{e.signal}</span>
                  <span>{e.severity}</span>
                  <span>{stream.processingStatusById[e.id] ?? "PROCESSING…"}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {activeTab === "incidents" && (
          <ActiveIncidentsView
            key={datasetVersion}
            dataSource={dataSource}
            onPushToast={pushToast}
            onIncidentApproved={(incidentId, title) => {
              if (dataSource !== "live") {
                recordDemoMemoryEvent({
                  event_type: "INCIDENT_PATTERN_APPROVED",
                  entity_id: incidentId,
                  decision: "Human-approved grouping",
                  pattern: title,
                });
              }
            }}
          />
        )}
        {activeTab === "tuning" && (
          <NoiseTuningView
            key={datasetVersion}
            dataSource={dataSource}
            onPushToast={pushToast}
            onRecommendationDecided={(ruleId, approved) => {
              if (dataSource !== "live" && approved) {
                recordDemoMemoryEvent({
                  event_type: "TUNING_RECOMMENDATION_APPROVED",
                  entity_id: ruleId,
                  decision: "Recommendation approved for review",
                  pattern: null,
                });
              }
            }}
          />
        )}
        {activeTab === "memory" && (
          <SeaMemoryView key={datasetVersion} dataSource={dataSource} demoMemoryLog={demoMemoryLog} />
        )}
        {activeTab === "datasets" && (
          <DatasetsView
            backendReachable={backendStatus === "REACHABLE"}
            onPushToast={pushToast}
            onDatasetSelected={() => setDatasetVersion((v) => v + 1)}
          />
        )}
      </main>

      <ToastNotifications toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}
