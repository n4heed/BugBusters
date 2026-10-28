// Real backend client. Every function here talks to the actual FastAPI
// service. Callers decide what to do when a call throws (ActiveIncidentsView
// etc. fall back to the demo adapter in src/data/fixtures.ts behind the same
// shapes) — this module never fabricates success.
import type {
  AIStatus,
  AIUsageSummary,
  Alert,
  ApprovalResult,
  DatasetSchemaResponse,
  DatasetSummary,
  EvaluationMetrics,
  EvaluationReport,
  FieldMappingEntry,
  GroupDetail,
  Incident,
  IncidentSummary,
  InvestigateIncidentResponse,
  ProcessingResult,
  ReplayResponse,
  ReplayRunSummary,
  RunStatsSummary,
  RunSummary,
  SeaMemoryEvent,
  StartRunResponse,
  TuningRecommendation,
} from "../types/domain";

export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

class ApiError extends Error {}

async function request<T>(path: string, init?: RequestInit, timeoutMs = 4000): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${API_BASE}${path}`, { ...init, signal: controller.signal });
    if (!res.ok) {
      let detail = "";
      try {
        detail = (await res.json())?.detail ?? "";
      } catch {
        // response body wasn't JSON — ignore, use the plain status message below
      }
      throw new ApiError(detail || `Request to ${path} failed with status ${res.status}`);
    }
    return (await res.json()) as T;
  } finally {
    clearTimeout(timeout);
  }
}

export const api = {
  health: () => request<{ status: string }>("/api/health"),
  listIncidents: () => request<IncidentSummary[]>("/api/incidents"),
  getIncident: (id: string) => request<Incident>(`/api/incidents/${id}`),
  approveIncident: (id: string) =>
    request<ApprovalResult>(`/api/incidents/${id}/approve`, { method: "POST" }),
  listSafetyQueue: () => request<Alert[]>("/api/incidents/safety-queue/alerts"),
  listTuning: () => request<TuningRecommendation[]>("/api/tuning"),
  approveTuning: (ruleId: string) =>
    request<ApprovalResult>(`/api/tuning/${ruleId}/approve`, { method: "POST" }),
  rejectTuning: (ruleId: string) =>
    request<ApprovalResult>(`/api/tuning/${ruleId}/reject`, { method: "POST" }),
  listMemory: () => request<SeaMemoryEvent[]>("/api/sea-memory"),
  getMetrics: () => request<EvaluationMetrics>("/api/metrics"),
  runPipelineReplay: () => request<ReplayResponse>("/api/pipeline/replay", { method: "POST" }),
  getAIStatus: () => request<AIStatus>("/api/pipeline/ai/status"),
  investigateIncident: (id: string) =>
    // A live model call (bounded retries + timeout) can legitimately take
    // longer than the default 4s request timeout — allow up to 45s.
    request<InvestigateIncidentResponse>(`/api/pipeline/incidents/${id}/investigate`, { method: "POST" }, 45000),
  getAIUsage: () => request<{ summary: AIUsageSummary; records: unknown[] }>("/api/pipeline/ai/usage"),
  getEvaluationReport: () =>
    // Runs real smoke tests, replay, and baseline comparisons through the
    // agent pipeline server-side — measured at ~20s, well over the default
    // 4s request timeout. Allow up to 40s.
    request<EvaluationReport>("/api/evaluation/report", undefined, 40000),

  // --- Dataset ingestion (Milestone 4) ---
  importDataset: (name: string, files: File[]) => {
    const form = new FormData();
    form.append("name", name);
    for (const f of files) form.append("files", f);
    return request<DatasetSummary>("/api/datasets/import", { method: "POST", body: form }, 20000);
  },
  listDatasets: () => request<DatasetSummary[]>("/api/datasets"),
  getDataset: (id: string) => request<DatasetSummary>(`/api/datasets/${id}`),
  getDatasetSchema: (id: string) => request<DatasetSchemaResponse>(`/api/datasets/${id}/schema`),
  confirmDatasetMapping: (id: string, fileName: string, category: string, fieldMappings: FieldMappingEntry[]) =>
    request<DatasetSummary>(`/api/datasets/${id}/mapping`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_name: fileName, category, field_mappings: fieldMappings }),
    }),
  validateDataset: (id: string) => request<DatasetSummary>(`/api/datasets/${id}/validate`, { method: "POST" }, 10000),
  loadDataset: (id: string) => request<DatasetSummary>(`/api/datasets/${id}/load`, { method: "POST" }),
  selectDataset: (id: string) => request<DatasetSummary>(`/api/datasets/${id}/select`, { method: "POST" }),
  replayDataset: (id: string) => request<ReplayRunSummary>(`/api/datasets/${id}/replay`, { method: "POST" }, 15000),
  listDatasetRuns: (id: string) => request<RunSummary[]>(`/api/datasets/${id}/runs`),
  getRunGroups: (id: string, runId: string) => request<GroupDetail[]>(`/api/datasets/${id}/runs/${runId}/groups`),
  evaluateRun: (id: string, runId: string) => request<Record<string, unknown>>(`/api/datasets/${id}/runs/${runId}/evaluate`, { method: "POST" }),

  // --- Phase 4: async processing + live dashboard polling ---
  startRun: (id: string) => request<StartRunResponse>(`/api/datasets/${id}/runs`, { method: "POST" }),
  getRunSummary: (id: string, runId: string) => request<RunStatsSummary>(`/api/datasets/${id}/runs/${runId}/summary`),
  getRunRecentResults: (id: string, runId: string, limit = 20) =>
    request<ProcessingResult[]>(`/api/datasets/${id}/runs/${runId}/recent?limit=${limit}`),
};

export { ApiError };
