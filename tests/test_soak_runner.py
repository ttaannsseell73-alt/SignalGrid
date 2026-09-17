import asyncio
import json

from signalgrid.ops.run_soak import run_soak_session
from signalgrid.runtime import RuntimeConfig, RuntimeMode
from signalgrid.state.store import StateStore


class FakeRuntime:
    def __init__(self, store, *, halt_after=None):
        self.store = store
        self.config = RuntimeConfig(RuntimeMode.PAPER, ("SOLUSDT",), str(store.path))
        self.halt_after = halt_after

    async def run(self, stop):
        self.store.set_runtime("runtime_mode", "PAPER")
        self.store.set_runtime("runtime_status", "PAPER_LIVE")
        self.store.set_runtime(
            "runtime_stats",
            json.dumps({"open_failures": 0, "signal_to_order_p95_ms": 25.0}),
        )
        if self.halt_after is None:
            await stop.wait()
        else:
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.halt_after)
            except TimeoutError:
                self.store.halt("TEST_HALT")
                await stop.wait()
        self.store.set_runtime("runtime_status", "HALTED" if self.store.halted() else "STOPPED")


def test_one_command_paper_soak_passes_with_continuous_samples(tmp_path):
    async def scenario():
        store = StateStore(tmp_path / "state.db")
        runtime = FakeRuntime(store)
        try:
            report = await run_soak_session(
                runtime,
                journal=tmp_path / "soak.jsonl",
                required_seconds=1.0,
                sample_interval_seconds=0.2,
                startup_timeout_seconds=1.0,
                max_gap_seconds=0.5,
            )
            assert report.passed
            assert report.sample_count >= 5
            assert report.gap_violation_count == 0
            assert report.max_open_failures == 0
        finally:
            store.close()
    asyncio.run(scenario())


def test_runner_fails_fast_when_runtime_halts(tmp_path):
    async def scenario():
        store = StateStore(tmp_path / "state.db")
        runtime = FakeRuntime(store, halt_after=0.12)
        try:
            report = await run_soak_session(
                runtime,
                journal=tmp_path / "soak.jsonl",
                required_seconds=2.0,
                sample_interval_seconds=0.05,
                startup_timeout_seconds=1.0,
                max_gap_seconds=0.2,
            )
            assert not report.passed
            assert report.halted_samples >= 1
            assert report.observed_seconds < 2.0
        finally:
            store.close()
    asyncio.run(scenario())
