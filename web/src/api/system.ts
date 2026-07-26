import { apiRequest, jsonBody } from "./client";
import type { components } from "./schema";

export type SchedulerPolicy = components["schemas"]["SchedulerPolicyView"];
export type SchedulerPolicyCommand = components["schemas"]["SchedulerPolicyCommand"];

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
}

export const getSystemStatus = () => apiRequest<SystemStatus>("/api/v1/system/status");
export const getSchedulerPolicy = () =>
  apiRequest<SchedulerPolicy>("/api/v1/system/scheduler-policy");
export const updateSchedulerPolicy = (command: SchedulerPolicyCommand) =>
  apiRequest<SchedulerPolicy>("/api/v1/system/scheduler-policy", {
    method: "PUT",
    body: jsonBody(command),
  });
