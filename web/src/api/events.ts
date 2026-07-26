export interface RunEvent {
  sequence: number;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface EventSourceLike {
  onmessage: ((event: MessageEvent<string>) => void) | null;
  onerror: ((event: Event) => void) | null;
  close(): void;
}

interface SubscriptionOptions {
  eventSourceFactory?: (url: string) => EventSourceLike;
}

const TERMINAL_EVENTS = new Set([
  "assessment.succeeded",
  "assessment.failed",
  "assessment.cancelled",
  "assessment.needs_attention",
]);
const RECONNECT_DELAYS = [1_000, 2_000, 5_000, 10_000];

function validEvent(value: unknown, after: number): value is RunEvent {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<RunEvent>;
  return (
    Number.isInteger(candidate.sequence) &&
    Number(candidate.sequence) > after &&
    typeof candidate.event_type === "string" &&
    typeof candidate.created_at === "string" &&
    typeof candidate.payload === "object" &&
    candidate.payload !== null &&
    !Array.isArray(candidate.payload)
  );
}

export function subscribeToRun(
  runId: string,
  lastSequence: number,
  onEvent: (event: RunEvent) => void,
  onDegraded: ((degraded: boolean) => void) | undefined = undefined,
  options: SubscriptionOptions = {},
): { close: () => void } {
  const factory = options.eventSourceFactory ?? ((url) => new EventSource(url));
  let source: EventSourceLike | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
  let sequence = lastSequence;
  let failures = 0;
  let closed = false;

  const connect = () => {
    if (closed) return;
    const connected = factory(
      `/api/v1/assessments/${encodeURIComponent(runId)}/events?after=${sequence}`,
    );
    source = connected;
    connected.onmessage = (message) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(message.data);
      } catch {
        return;
      }
      if (!validEvent(parsed, sequence)) return;
      sequence = parsed.sequence;
      failures = 0;
      onDegraded?.(false);
      onEvent(parsed);
      if (TERMINAL_EVENTS.has(parsed.event_type)) {
        closed = true;
        source?.close();
      }
    };
    connected.onerror = () => {
      if (closed) return;
      source?.close();
      failures += 1;
      if (failures >= 3) onDegraded?.(true);
      const delay = RECONNECT_DELAYS[Math.min(failures - 1, RECONNECT_DELAYS.length - 1)];
      reconnectTimer = setTimeout(connect, delay);
    };
  };

  connect();
  return {
    close: () => {
      closed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      source?.close();
    },
  };
}
