from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Iterable

from signalgrid.engine import SignalGridEngine
from signalgrid.ops.run_scalping_demo import (
    DEFAULT_SCALPING_SYMBOLS,
    SCALPING_GRID_CONFIG,
    SCALPING_RISK_CONFIG,
    SCALPING_SIGNAL_CONFIG,
)
from signalgrid.ops.run_soak import run_soak_session
from signalgrid.risk.engine import RiskEngine
from signalgrid.runtime import RuntimeConfig, RuntimeMode, SignalGridRuntime
from signalgrid.scanner import MultiSymbolScanner, ScannerConfig
from signalgrid.sonar.impulse_radar import ImpulseRadar
from signalgrid.signals.scalping import SCALPING_PROFILE_VERSION, ScalpingSignalEngine


def _parse_symbols(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(x.strip().upper() for x in text.split(",") if x.strip()))


def build_scalping_paper_runtime(symbols: tuple[str, ...], db_path: str) -> SignalGridRuntime:
    runtime = SignalGridRuntime.from_environment(
        RuntimeConfig(
            mode=RuntimeMode.PAPER,
            symbols=symbols,
            db_path=db_path,
            warmup_bars=60,
            cleanup_interval_seconds=0.5,
        )
    )
    base = runtime.scanner
    runtime.scanner = MultiSymbolScanner(
        runtime.router,
        SignalGridEngine(
            signal_engine=ScalpingSignalEngine(SCALPING_SIGNAL_CONFIG),
            risk_engine=RiskEngine(SCALPING_RISK_CONFIG),
        ),
        ScannerConfig(
            max_symbols=20,
            min_evaluation_interval_ms=75,
            max_market_data_age_ms=1_000,
            signal_debounce_ms=1_200,
        ),
        positions_provider=base.positions_provider,
        now_ms=base.now_ms,
        impulse_radar=ImpulseRadar(),
    )
    runtime.grid_config = SCALPING_GRID_CONFIG
    runtime.store.set_runtime("strategy_profile", SCALPING_PROFILE_VERSION)
    runtime.store.set_runtime("environment_label", "BINANCE_PUBLIC_PAPER")
    runtime.store.set_runtime("coin_sonar_v2", "ENABLED")
    runtime.store.set_runtime("execution_mode", "PAPER_ONLY")
    return runtime


async def _run(runtime: SignalGridRuntime, *, journal: Path, required_seconds: float, sample_interval_seconds: float):
    return await run_soak_session(
        runtime,
        journal=journal,
        required_seconds=required_seconds,
        sample_interval_seconds=sample_interval_seconds,
        startup_timeout_seconds=120.0,
    )


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="SignalGrid Scalping V1 canonical forward PAPER validation"
    )
    parser.add_argument("--symbols", default=",".join(DEFAULT_SCALPING_SYMBOLS))
    parser.add_argument("--hours", type=float, default=1.0)
    parser.add_argument("--sample-seconds", type=float, default=15.0)
    parser.add_argument("--db", default="signalgrid-scalping-paper.db")
    parser.add_argument("--journal", default="scalping-paper.jsonl")
    parser.add_argument("--overwrite-journal", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.hours <= 0:
        parser.error("--hours must be positive")
    if args.sample_seconds <= 0:
        parser.error("--sample-seconds must be positive")

    symbols = _parse_symbols(args.symbols)
    if not symbols:
        parser.error("at least one symbol is required")
    if len(symbols) > 20:
        parser.error("scalping V1 supports at most 20 symbols")

    journal = Path(args.journal)
    if journal.exists() and journal.stat().st_size > 0:
        if not args.overwrite_journal:
            parser.error("journal already contains samples; use --overwrite-journal")
        journal.unlink()

    runtime = build_scalping_paper_runtime(symbols, args.db)
    try:
        report = asyncio.run(
            _run(
                runtime,
                journal=journal,
                required_seconds=args.hours * 3600.0,
                sample_interval_seconds=args.sample_seconds,
            )
        )
        payload = {
            "strategy": "SCALPING_V1",
            "environment": "BINANCE_PUBLIC_PAPER",
            "orders": "PAPER_ONLY",
            "coin_sonar_v2": "ENABLED",
            "symbols": list(symbols),
            "runtime": runtime.stats.snapshot(),
            "soak": report.to_dict(),
        }
        print(json.dumps(payload, indent=2 if args.pretty else None, sort_keys=True))
        return 0 if report.passed else 2
    finally:
        runtime.store.close()


if __name__ == "__main__":
    raise SystemExit(main())
