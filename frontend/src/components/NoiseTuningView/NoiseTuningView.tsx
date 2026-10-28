import { useEffect, useState } from "react";
import type { DataSource, TuningRecommendation } from "../../types/domain";
import { api } from "../../api/client";
import { demoTuningRecommendations } from "../../data/fixtures";
import { TuningSummary } from "./TuningSummary";
import { TuningRecommendationsTable } from "./TuningRecommendationsTable";
import { DemoBadge } from "../common/DemoBadge";
import type { Toast } from "../ToastNotifications";

export function NoiseTuningView({
  dataSource,
  onPushToast,
  onRecommendationDecided,
}: {
  dataSource: DataSource;
  onPushToast: (toast: Omit<Toast, "id">) => void;
  onRecommendationDecided: (ruleId: string, approved: boolean) => void;
}) {
  const [recommendations, setRecommendations] = useState<TuningRecommendation[]>([]);
  const [loading, setLoading] = useState(true);
  const [isDemo, setIsDemo] = useState(dataSource === "demo");

  useEffect(() => {
    let active = true;
    async function load() {
      setLoading(true);
      if (dataSource === "live") {
        try {
          const list = await api.listTuning();
          if (active) {
            setRecommendations(list);
            setIsDemo(false);
            setLoading(false);
          }
          return;
        } catch {
          if (!active) return;
          setIsDemo(true);
        }
      } else {
        setIsDemo(true);
      }
      setRecommendations(demoTuningRecommendations);
      setLoading(false);
    }
    load();
    return () => {
      active = false;
    };
  }, [dataSource]);

  async function approve(ruleId: string) {
    try {
      if (!isDemo) await api.approveTuning(ruleId);
      setRecommendations((prev) =>
        prev.map((r) => (r.rule_id === ruleId ? { ...r, status: "approved" } : r)),
      );
      onRecommendationDecided(ruleId, true);
      onPushToast({
        kind: "success",
        message: isDemo
          ? "APPROVED IN DEMO — NOT APPLIED TO PRODUCTION."
          : "Recommendation approved and recorded.",
      });
    } catch {
      onPushToast({ kind: "error", message: "Approval failed — recommendation left unchanged." });
    }
  }

  async function reject(ruleId: string) {
    try {
      if (!isDemo) await api.rejectTuning(ruleId);
      setRecommendations((prev) =>
        prev.map((r) => (r.rule_id === ruleId ? { ...r, status: "rejected" } : r)),
      );
      onPushToast({ kind: "info", message: "Recommendation rejected." });
    } catch {
      onPushToast({ kind: "error", message: "Rejection failed — recommendation left unchanged." });
    }
  }

  if (loading) return <p className="loading-text">Loading tuning recommendations…</p>;

  const totalRecurring = recommendations.reduce((sum, r) => sum + r.count_24h, 0);

  return (
    <div>
      <h2 className="section-heading">Triage Agent // Chronic Alert Noise &amp; Tuning</h2>
      <p className="section-subtitle">
        Recurring alert patterns with no confirmed link in the available incident history.
      </p>
      {isDemo && (
        <div style={{ marginBottom: 12 }}>
          <DemoBadge />
        </div>
      )}

      <div className="safety-lock-banner">
        SAFETY LOCK: Recommendations require human review. The prototype never silently closes or mutes alerts.
      </div>

      <TuningSummary recommendations={recommendations} totalRecurring={totalRecurring} />
      <TuningRecommendationsTable recommendations={recommendations} onApprove={approve} onReject={reject} />
    </div>
  );
}
