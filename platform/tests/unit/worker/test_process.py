import asyncio
import signal

from tradingng_platform.worker.process import CancellationController, ProcessIdentity


class _Clock:
    now = 0.0

    def __call__(self):
        return self.now


class _ControlledSleep:
    def __init__(self, clock):
        self.clock = clock
        self.calls = asyncio.Queue()
        self.releases = asyncio.Queue()

    async def __call__(self, delay):
        await self.calls.put(delay)
        await self.releases.get()
        self.clock.now += delay


async def test_cancellation_targets_only_exact_recorded_process_group():
    clock = _Clock()
    sleep = _ControlledSleep(clock)
    signals = []
    target = ProcessIdentity(pid=101, pgid=201, start_time_ticks=301)
    unrelated_pgid = 999
    controller = CancellationController(
        identity_matches=lambda identity: identity == target,
        signal_group=lambda pgid, sig: signals.append((pgid, sig)),
        clock=clock,
        sleep=sleep,
    )

    cancellation = asyncio.create_task(controller.cancel(target, requested_at=clock()))
    assert await sleep.calls.get() == 30.0
    assert signals == []

    sleep.releases.put_nowait(None)
    assert await sleep.calls.get() == 10.0
    assert signals == [(target.pgid, signal.SIGTERM)]

    sleep.releases.put_nowait(None)
    await cancellation
    assert signals == [
        (target.pgid, signal.SIGTERM),
        (target.pgid, signal.SIGKILL),
    ]
    assert all(pgid != unrelated_pgid for pgid, _ in signals)


async def test_cancellation_refuses_reused_or_mismatched_pid():
    target = ProcessIdentity(pid=101, pgid=201, start_time_ticks=301)
    signals = []
    controller = CancellationController(
        identity_matches=lambda identity: False,
        signal_group=lambda pgid, sig: signals.append((pgid, sig)),
        sleep=lambda delay: asyncio.sleep(0),
    )

    await controller.cancel(target, requested_at=0)

    assert signals == []
