import { useState } from "react";
import type { TuningRecommendation } from "../../types/domain";
import { StatusTag } from "../common/StatusTag";
import { ConfirmationDialog } from "../ConfirmationDialog";

export function TuningRecommendationsTable({
  recommendations,
  onApprove,
  onReject,
}: {
  recommendations: TuningRecommendation[];
  onApprove: (ruleId: string) => Promise<void>;
  onReject: (ruleId: string) => Promise<void>;
}) {
  const [filter, setFilter] = useState("");
  const [pendingAction, setPendingAction] = useState<{ rec: TuningRecommendation; action: "approve" | "reject" } | null>(
    null,
  );
  const [working, setWorking] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const visible = recommendations.filter(
    (r) =>
      r.rule_id.toLowerCase().includes(filter.toLowerCase()) ||
      r.service.toLowerCase().includes(filter.toLowerCase()) ||
      r.rule_name.toLowerCase().includes(filter.toLowerCase()),
  );

  async function confirm() {
    if (!pendingAction) return;
    setWorking(true);
    try {
      if (pendingAction.action === "approve") await onApprove(pendingAction.rec.rule_id);
      else await onReject(pendingAction.rec.rule_id);
    } finally {
      setWorking(false);
      setPendingAction(null);
    }
  }

  return (
    <section className="panel">
      <div className="filter-row">
        <input
          className="filter-input"
          placeholder="Filter by rule ID, service or rule name…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          aria-label="Filter tuning recommendations"
        />
      </div>

      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Rule ID</th>
              <th>Service</th>
              <th>Rule Name</th>
              <th>24H Count</th>
              <th>Historical Links</th>
              <th>Suggested Action</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((r) => (
              <tr key={r.rule_id}>
                <td>{r.rule_id}</td>
                <td>{r.service}</td>
                <td>{r.rule_name}</td>
                <td>{r.count_24h}</td>
                <td>{r.historical_links} confirmed</td>
                <td style={{ maxWidth: 220 }}>{r.suggested_action}</td>
                <td>
                  <StatusTag status={r.status} />
                </td>
                <td>
                  <div className="row-actions">
                    <button
                      className="btn btn-sm btn-secondary"
                      onClick={() => setExpandedId(expandedId === r.rule_id ? null : r.rule_id)}
                    >
                      Review
                    </button>
                    <button
                      className="btn btn-sm"
                      disabled={r.status !== "pending"}
                      onClick={() => setPendingAction({ rec: r, action: "approve" })}
                    >
                      Approve
                    </button>
                    <button
                      className="btn btn-sm btn-danger"
                      disabled={r.status !== "pending"}
                      onClick={() => setPendingAction({ rec: r, action: "reject" })}
                    >
                      Reject
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {expandedId && (
        <div className="panel" style={{ marginTop: 12 }}>
          {(() => {
            const r = recommendations.find((x) => x.rule_id === expandedId);
            if (!r) return null;
            return (
              <>
                <h3 className="section-heading">{r.rule_id} — {r.rule_name}</h3>
                <p>
                  Service <strong>{r.service}</strong> fired {r.count_24h} times in the last 24 hours with{" "}
                  {r.historical_links} confirmed historical incident link(s). No confirmed incident link found in
                  available history does not mean this alert is false — only that no match has been established yet.
                </p>
                <p>{r.suggested_action}</p>
              </>
            );
          })()}
        </div>
      )}

      {pendingAction && (
        <ConfirmationDialog
          title={pendingAction.action === "approve" ? "Approve Recommendation" : "Reject Recommendation"}
          confirmLabel={pendingAction.action === "approve" ? "Approve" : "Reject"}
          pending={working}
          onConfirm={confirm}
          onCancel={() => setPendingAction(null)}
        >
          <p>
            <strong>{pendingAction.rec.rule_id}</strong> — {pendingAction.rec.suggested_action}
          </p>
          {pendingAction.action === "approve" ? (
            <p>
              Approving records this recommendation as reviewed. It does not automatically mute or suppress the
              alert rule, and no production threshold is changed by this action.
            </p>
          ) : (
            <p>This marks the recommendation as rejected on review. No change is made to the alert rule.</p>
          )}
        </ConfirmationDialog>
      )}
    </section>
  );
}
