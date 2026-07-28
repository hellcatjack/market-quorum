import { apiRequest, jsonBody } from "./client";
import type { components } from "./schema";

export type SchedulerPolicy = components["schemas"]["SchedulerPolicyView"];
export type SchedulerPolicyCommand = components["schemas"]["SchedulerPolicyCommand"];

export type ModelRoutingPolicy = components["schemas"]["ModelRoutingPolicyView"];
export type ModelRoutingPolicyCommand =
  components["schemas"]["ModelRoutingPolicyCommand"];

export interface SystemStatus {
  gateway: {
    status: string;
    active_completions: number;
    model: string;
    reasoning_effort: string;
    snapshot_id: string;
    latency_ms: number;
  };
  workers: Array<{
    instance_name: string;
    status: string;
    heartbeat_at: string;
    capabilities: Record<string, unknown>;
  }>;
  circuits: Array<{
    name: string;
    status: string;
    failure_count: number;
    opened_until: string | null;
    last_error_code: string | null;
  }>;
  alpha_vantage: {
    status: "normal" | "cooldown" | "half_open" | "unavailable";
    configured_requests_per_minute: number;
    effective_requests_per_minute: number;
    max_in_flight: number;
    in_flight: number;
    queued: number;
    oldest_queued_seconds: number | null;
    blocked_until: string | null;
    requests: number;
    upstream_requests: number;
    cache_hits: number;
    coalesced_requests: number;
    rate_limits: number;
    transient_errors: number;
  } | null;
}

export const getSystemStatus = () => apiRequest<SystemStatus>("/api/v1/system/status");
export const getSchedulerPolicy = () =>
  apiRequest<SchedulerPolicy>("/api/v1/system/scheduler-policy");
export const updateSchedulerPolicy = (command: SchedulerPolicyCommand) =>
  apiRequest<SchedulerPolicy>("/api/v1/system/scheduler-policy", {
    method: "PUT",
    body: jsonBody(command),
  });
export const getModelRoutingPolicy = () =>
  apiRequest<ModelRoutingPolicy>("/api/v1/system/model-routing");
export const updateModelRoutingPolicy = (command: ModelRoutingPolicyCommand) =>
  apiRequest<ModelRoutingPolicy>("/api/v1/system/model-routing", {
    method: "PUT",
    body: jsonBody(command),
  });
