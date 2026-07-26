import { apiRequest, apiTextRequest, jsonBody } from "./client";
import type { components } from "./schema";

export type RunDetail = components["schemas"]["RunDetailView"];
export type RunStep = components["schemas"]["RunStepView"];
export type Decision = components["schemas"]["DecisionView"];
export type Evidence = components["schemas"]["EvidenceView"];
export type Artifact = components["schemas"]["ArtifactView"];
export type Review = components["schemas"]["ReviewView"];
export type Comment = components["schemas"]["CommentView"];
export type InstrumentSummary = components["schemas"]["InstrumentSummaryView"];
export type InstrumentHistoryItem = components["schemas"]["InstrumentHistoryItem"];
export type Validation = components["schemas"]["ValidationView"];

export interface CurrentUser {
  subject: string;
  display_name: string;
  scopes: string[];
  roles: string[];
}

export const getCurrentUser = () => apiRequest<CurrentUser>("/api/v1/me");
export const getRun = (runId: string) =>
  apiRequest<RunDetail>(`/api/v1/assessments/${encodeURIComponent(runId)}`);
export const getSteps = (runId: string) =>
  apiRequest<RunStep[]>(`/api/v1/assessments/${encodeURIComponent(runId)}/steps`);
export const getDecision = (runId: string) =>
  apiRequest<Decision>(`/api/v1/assessments/${encodeURIComponent(runId)}/decision`);
export const getEvidence = (runId: string) =>
  apiRequest<Evidence[]>(`/api/v1/assessments/${encodeURIComponent(runId)}/evidence`);
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
