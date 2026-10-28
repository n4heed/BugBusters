import type { TuningRecommendation } from "../../types/domain";

export function TuningSummary({ recommendations, totalRecurring }: { recommendations: TuningRecommendation[]; totalRecurring: number }) {
  const distinctRules = recommendations.length;
  const pending = recommendations.filter((r) => r.status === "pending").length;
  const approved = recommendations.filter((r) => r.status === "approved").length;
  const linked = recommendations.reduce((sum, r) => sum + r.historical_links, 0);

  return (
    <div className="summary-cards">
      <div className="summary-card">
        <span className="kpi-label">Recurring Alerts Under Review</span>
        <div className="kpi-value">{totalRecurring}</div>
      </div>
      <div className="summary-card">
        <span className="kpi-label">Distinct Alert Rules</span>
        <div className="kpi-value">{distinctRules}</div>
      </div>
      <div className="summary-card">
        <span className="kpi-label">Historical Incident Links</span>
        <div className="kpi-value">{linked}</div>
      </div>
      <div className="summary-card">
        <span className="kpi-label">Pending Recommendations</span>
        <div className="kpi-value">{pending}</div>
      </div>
      <div className="summary-card">
        <span className="kpi-label">Approved Recommendations</span>
        <div className="kpi-value">{approved}</div>
      </div>
    </div>
  );
}
