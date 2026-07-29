import { apiRequest, jsonBody } from "./client";
import type { components } from "./schema";

export type AssessmentRun = components["schemas"]["RunView"];
export type AssessmentRunPage = components["schemas"]["RunPage"];
export type AssessmentStatus = components["schemas"]["RunStatus"];
export type Capacity = components["schemas"]["CapacityView"];
export type SubmitAssessmentBatch = components["schemas"]["SubmitAssessments"];

export interface AdmissionSummary {
  running: number;
  max_running: number;
  queued: number;
  oldest_queued_seconds: number | null;
  admission: "immediate" | "queued" | "paused";
  reason: "capacity_available" | "capacity_busy" | "temporarily_paused";
}

export interface AssessmentFilters {
  ticker?: string;
  statuses?: AssessmentStatus[];
  createdFrom?: string;
  createdTo?: string;
  cursor?: string;
  limit?: number;
}

export async function submitAssessmentBatch(
  payload: SubmitAssessmentBatch,
): Promise<AssessmentRunPage> {
  return apiRequest<AssessmentRunPage>("/api/v1/assessment-batches", {
    method: "POST",
    body: jsonBody(payload),
  });
}

export async function listAssessments(
  filters: AssessmentFilters = {},
): Promise<AssessmentRunPage> {
  const query = new URLSearchParams();
  if (filters.ticker) query.set("ticker", filters.ticker);
  for (const status of filters.statuses ?? []) query.append("status", status);
  if (filters.createdFrom) query.set("created_from", filters.createdFrom);
  if (filters.createdTo) query.set("created_to", filters.createdTo);
  if (filters.cursor) query.set("cursor", filters.cursor);
  query.set("limit", String(filters.limit ?? 50));
  return apiRequest<AssessmentRunPage>(`/api/v1/assessments?${query.toString()}`);
}

export async function getCapacity(): Promise<Capacity> {
  return apiRequest<Capacity>("/api/v1/system/capacity");
}

export async function getAdmissionSummary(): Promise<AdmissionSummary> {
  return apiRequest<AdmissionSummary>("/api/v1/assessments/admission-summary");
}
