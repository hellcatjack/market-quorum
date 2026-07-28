import { ApiClientError, apiRequest, apiTextRequest, jsonBody } from "./client";
import type { components } from "./schema";

export type RunDetail = components["schemas"]["RunDetailView"];
export type Run = components["schemas"]["RunView"];
export type RunStep = components["schemas"]["RunStepView"];
export type Decision = components["schemas"]["DecisionView"];
export type Evidence = components["schemas"]["EvidenceView"];
export type LlmInteraction = components["schemas"]["LlmInteractionView"];
export type LlmInteractionPage = components["schemas"]["LlmInteractionPage"];
export type Artifact = components["schemas"]["ArtifactView"];
export type Review = components["schemas"]["ReviewView"];
export type Comment = components["schemas"]["CommentView"];
export type InstrumentSummary = components["schemas"]["InstrumentSummaryView"];
export type InstrumentHistoryItem = components["schemas"]["InstrumentHistoryItem"];
export type InstrumentOverview = components["schemas"]["InstrumentOverviewItem"];
export type InstrumentOverviewPage = components["schemas"]["InstrumentOverviewPage"];
export type Validation = components["schemas"]["ValidationView"];
export type Integrity = components["schemas"]["IntegrityView"];
export type IntegritySummary = components["schemas"]["IntegritySummaryView"];

export interface InstrumentOverviewFilters {
  query?: string;
  assetType?: components["schemas"]["AssetType"];
  statuses?: components["schemas"]["RunStatus"][];
  anomalousOnly?: boolean;
  createdFrom?: string;
  createdTo?: string;
  cursor?: string;
  limit?: number;
}

export interface CurrentUser {
  subject: string;
  display_name: string;
  scopes: string[];
  roles: string[];
}

export const getCurrentUser = () => apiRequest<CurrentUser>("/api/v1/me");
export const getRun = (runId: string) =>
  apiRequest<RunDetail>(`/api/v1/assessments/${encodeURIComponent(runId)}`);
export const getIntegrity = (runId: string) =>
  apiRequest<Integrity>(`/api/v1/assessments/${encodeURIComponent(runId)}/integrity`);
export const getIntegritySummary = () =>
  apiRequest<IntegritySummary>("/api/v1/integrity/summary");
export const getSteps = (runId: string) =>
  apiRequest<RunStep[]>(`/api/v1/assessments/${encodeURIComponent(runId)}/steps`);
export const getDecision = (runId: string) =>
  apiRequest<Decision>(`/api/v1/assessments/${encodeURIComponent(runId)}/decision`);
export const getEvidence = (runId: string) =>
  apiRequest<Evidence[]>(`/api/v1/assessments/${encodeURIComponent(runId)}/evidence`);
export const getLlmInteractions = (runId: string) =>
  apiRequest<LlmInteractionPage>(
    `/api/v1/assessments/${encodeURIComponent(runId)}/llm-interactions`,
  );
export const getArtifacts = (runId: string) =>
  apiRequest<Artifact[]>(`/api/v1/assessments/${encodeURIComponent(runId)}/artifacts`);
export const getValidations = (runId: string) =>
  apiRequest<Validation[]>(`/api/v1/assessments/${encodeURIComponent(runId)}/validations`);
export const getArtifactContent = (artifactId: string) =>
  apiTextRequest(`/api/v1/artifacts/${encodeURIComponent(artifactId)}`);
export const getReviews = (runId: string) =>
  apiRequest<Review[]>(`/api/v1/assessments/${encodeURIComponent(runId)}/reviews`);
export const getComments = (runId: string) =>
  apiRequest<Comment[]>(`/api/v1/assessments/${encodeURIComponent(runId)}/comments`);
export const cancelRun = (runId: string) =>
  apiRequest<RunDetail>(`/api/v1/assessments/${encodeURIComponent(runId)}/cancel`, {
    method: "POST",
    body: jsonBody({}),
  });
export const retryRun = (runId: string) =>
  apiRequest<RunDetail>(`/api/v1/assessments/${encodeURIComponent(runId)}/retry`, {
    method: "POST",
    body: jsonBody({}),
  });
export const deleteRun = async (runId: string) => {
  try {
    await apiRequest<void>(`/api/v1/assessments/${encodeURIComponent(runId)}`, {
      method: "DELETE",
    });
  } catch (error) {
    if (
      error instanceof ApiClientError
      && error.status === 404
      && error.code === "assessment_not_found"
    ) return;
    throw error;
  }
};
export const cleanReassessRun = (runId: string) =>
  apiRequest<Run>(`/api/v1/assessments/${encodeURIComponent(runId)}/clean-reassessment`, {
    method: "POST",
    body: jsonBody({}),
  });
export const createComment = (runId: string, body: string) =>
  apiRequest<Comment>(`/api/v1/assessments/${encodeURIComponent(runId)}/comments`, {
    method: "POST",
    body: jsonBody({ body }),
  });
export const createReview = (runId: string, verdict: string, comment: string) =>
  apiRequest<Review>(`/api/v1/assessments/${encodeURIComponent(runId)}/reviews`, {
    method: "POST",
    body: jsonBody({ verdict, comment }),
  });
export const getInstrument = (ticker: string) =>
  apiRequest<InstrumentSummary>(`/api/v1/instruments/${encodeURIComponent(ticker)}`);
export const getInstrumentHistory = (ticker: string) =>
  apiRequest<InstrumentHistoryItem[]>(
    `/api/v1/instruments/${encodeURIComponent(ticker)}/history`,
  );

export const listInstrumentOverviews = (
  filters: InstrumentOverviewFilters = {},
) => {
  const query = new URLSearchParams();
  if (filters.query) query.set("query", filters.query);
  if (filters.assetType) query.set("asset_type", filters.assetType);
  for (const status of filters.statuses ?? []) query.append("status", status);
  if (filters.anomalousOnly) query.set("anomalous_only", "true");
  if (filters.createdFrom) query.set("created_from", filters.createdFrom);
  if (filters.createdTo) query.set("created_to", filters.createdTo);
  if (filters.cursor) query.set("cursor", filters.cursor);
  query.set("limit", String(filters.limit ?? 50));
  return apiRequest<InstrumentOverviewPage>(
    `/api/v1/instrument-overviews?${query.toString()}`,
  );
};
