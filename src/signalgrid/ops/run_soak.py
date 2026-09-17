from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from time import monotonic
from typing import Iterable

from signalgrid.ops.health import collect_health
from signalgrid.ops.soak import SoakReport, append_sample, evaluate_soak, load_samples
from signalgrid.runtime import RuntimeConfig, RuntimeMode, SignalGridRuntime


async def _wait_runtime_live(
    runtime: SignalGridRuntime,
    runtime_task: asyncio.Task[None],
    startup_timeout_seconds: float,
) -> None:
    desired = "PAPER_LIVE" if runtime.config.mode is RuntimeMode.PAPER else "TESTNET_LIVE"
    deadline = monotonic() + startup_timeout_seconds
    while monotonic() < deadline:
        if runtime.store.halted():
            raise RuntimeError(runtime.store.halt_reason() or "runtime halted during startup")
        status = runtime.store.get_runtime("runtime_status", "UNKNOWN") or "UNKNOWN"
        if status == desired:
            return
        if runtime_task.done():
            exc = runtime_task.exception()
            if exc is not None:
                raise RuntimeError("runtime ended during startup") from exc
            raise RuntimeError("runtime ended before reaching LIVE status")
        await asyncio.sleep(0.05)
    raise TimeoutError(f"timed out waiting for {desired}")


async def run_soak_session(
    runtime: SignalGridRuntime,
    *,
    journal: str | Path,
    required_seconds: float,
    sample_interval_seconds: float = 60.0,
    startup_timeout_seconds: float = 120.0,
    max_gap_seconds: float | None = None,
    fail_fast: bool = True,
) -> SoakReport:
    if required_seconds <= 0:
        raise ValueError("required_seconds must be positive")
    if sample_interval_seconds <= 0:
        raise ValueError("sample_interval_seconds must be positive")
    if startup_timeout_seconds <= 0:
        raise ValueError("startup_timeout_seconds must be positive")

    allowed_gap = max_gap_seconds if max_gap_seconds is not None else sample_interval_seconds * 2.5
    if allowed_gap <= sample_interval_seconds:
        raise ValueError("max_gap_seconds must be greater than sample_interval_seconds")

    stop = asyncio.Event()
    runtime_task = asyncio.create_task(runtime.run(stop), name="signalgrid-soak-runtime")
    started = monotonic()
    try:
        await _wait_runtime_live(runtime, runtime_task, startup_timeout_seconds)
        started = monotonic()
        first = append_sample(journal, collect_health(runtime.store))
        if fail_fast and not first.health.get("healthy", False):
            stop.set()

        while not stop.is_set() and monotonic() - started < required_seconds:
            remaining = required_seconds - (monotonic() - started)
            wait_seconds = min(sample_interval_seconds, max(0.0, remaining))
            if wait_seconds <= 0:
                break
            try:
                await asyncio.wait_for(stop.wait(), timeout=wait_seconds)
                break
            except TimeoutError:
                pass

            if runtime_task.done():
                exc = runtime_task.exception()
                if exc is not None:
                    raise RuntimeError("runtime failed during soak") from exc
                break

            sample = append_sample(journal, collect_health(runtime.store))
            if fail_fast and not sample.health.get("healthy", False):
                stop.set()
                break

        if not runtime_task.done() and not stop.is_set():
            # Always capture the end boundary so duration coverage is explicit.
            append_sample(journal, collect_health(runtime.store))
        stop.set()
        await runtime_task
    finally:
        stop.set()
        if not runtime_task.done():
            runtime_task.cancel()
            await asyncio.gather(runtime_task, return_exceptions=True)

    return evaluate_soak(
        load_samples(journal),
        int(required_seconds),
        max_gap_seconds=allowed_gap,
    )


def _parse_symbols(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(x.strip().upper() for x in text.split(",") if x.strip()))


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run SignalGrid runtime and soak telemetry together")
    parser.add_argument("--mode", choices=("paper", "testnet"), required=True)
    parser.add_argument("--symbols", required=True, help="comma-separated Binance USD-M symbols")
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--sample-seconds", type=float, default=60.0)
    parser.add_argument("--max-gap-seconds", type=float, default=None)
    parser.add_argument("--startup-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--db", default="signalgrid.db")
    parser.add_argument("--journal", default="soak.jsonl")
    parser.add_argument("--overwrite-journal", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.hours <= 0:
        parser.error("--hours must be positive")
    journal = Path(args.journal)
    if journal.exists() and journal.stat().st_size > 0:
        if not args.overwrite_journal:
            parser.error("journal already contains samples; use a new path or --overwrite-journal")
        journal.unlink()

    mode = RuntimeMode.PAPER if args.mode == "paper" else RuntimeMode.TESTNET
    config = RuntimeConfig(mode=mode, symbols=_parse_symbols(args.symbols), db_path=args.db)
    runtime = SignalGridRuntime.from_environment(config)
    try:
        report = asyncio.run(
            run_soak_session(
                runtime,
                journal=journal,
                required_seconds=args.hours * 3600.0,
                sample_interval_seconds=args.sample_seconds,
                startup_timeout_seconds=args.startup_timeout_seconds,
                max_gap_seconds=args.max_gap_seconds,
            )
        )
        print(json.dumps(report.to_dict(), indent=2 if args.pretty else None, sort_keys=True))
        return 0 if report.passed else 2
    finally:
        runtime.store.close()


if __name__ == "__main__":
    raise SystemExit(main())
