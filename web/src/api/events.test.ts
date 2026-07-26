import { subscribeToRun, type EventSourceLike } from "./events";

class FakeEventSource implements EventSourceLike {
  static instances: FakeEventSource[] = [];
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  close = vi.fn();

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  message(value: unknown) {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(value) }));
  }

  error() {
    this.onerror?.(new Event("error"));
  }
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.useFakeTimers();
});

afterEach(() => vi.useRealTimers());

test("reconnects after the last validated sequence and reports degraded mode", () => {
  const events = vi.fn();
  const degraded = vi.fn();
  subscribeToRun("run-123", 2, events, degraded, {
    eventSourceFactory: (url) => new FakeEventSource(url),
  });

  expect(FakeEventSource.instances[0].url).toBe("/api/v1/assessments/run-123/events?after=2");
  FakeEventSource.instances[0].message({
    sequence: 4,
    event_type: "runner.stage.running_analysts",
    payload: { status: "running_analysts" },
    created_at: "2026-07-25T12:00:00Z",
  });
  FakeEventSource.instances[0].message({ sequence: "bad" });
  FakeEventSource.instances[0].error();
  vi.advanceTimersByTime(1_000);
  expect(FakeEventSource.instances[1].url).toContain("after=4");

  FakeEventSource.instances[1].error();
  vi.advanceTimersByTime(2_000);
  FakeEventSource.instances[2].error();
  expect(degraded).toHaveBeenCalledWith(true);
  expect(events).toHaveBeenCalledTimes(1);
});

test("stops reconnecting after a terminal event", () => {
  const events = vi.fn();
  const subscription = subscribeToRun("run-123", 0, events, undefined, {
    eventSourceFactory: (url) => new FakeEventSource(url),
  });
  FakeEventSource.instances[0].message({
    sequence: 7,
    event_type: "assessment.succeeded",
    payload: {},
    created_at: "2026-07-25T12:00:00Z",
  });

  expect(FakeEventSource.instances[0].close).toHaveBeenCalledOnce();
  FakeEventSource.instances[0].error();
  vi.runAllTimers();
  expect(FakeEventSource.instances).toHaveLength(1);
  subscription.close();
});
