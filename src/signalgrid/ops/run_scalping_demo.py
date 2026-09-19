from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Iterable

from signalgrid.engine import SignalGridEngine
from signalgrid.execution.grid import GridConfig
from signalgrid.ops.run_demo_reflex import (
    DEMO_REST_URL,
    DEMO_WS_STREAM_URL,
    _configure_demo_compat,
    _demo_rest_api,
    preflight_demo,
)
from signalgrid.ops.run_soak import run_soak_session
from signalgrid.risk.engine import RiskConfig, RiskEngine
from signalgrid.runtime import RuntimeConfig, RuntimeMode, SignalGridRuntime
from signalgrid.scanner import MultiSymbolScanner, ScannerConfig
from signalgrid.signals.scalping import (
    SCALPING_PROFILE_VERSION,
    ScalpingConfig,
    ScalpingSignalEngine,
)


DEFAULT_SCALPING_SYMBOLS = ("BTCUSDT",)

SCALPING_SIGNAL_CONFIG = ScalpingConfig(
    structure_lookback=12,
    compression_lookback=8,
    compression_baseline=30,
    compression_ratio_max=0.78,
    retest_tolerance_bps=8.0,
    max_spread_bps=4.0,
    min_natr_bps=4.0,
    max_natr_bps=180.0,
    min_expansion=0.90,
    strong_expansion=1.35,
    min_directional_flow=0.03,
    min_score=0.62,
    min_stop_bps=3.0,
    max_stop_bps=120.0,
    ttl_seconds=3.0,
)

SCALPING_RISK_CONFIG = RiskConfig(
    max_positions=3,
    max_total_notional_usdt=360.0,
    base_notional_usdt=60.0,
    max_notional_per_trade_usdt=120.0,
    leverage=3,
)

SCALPING_GRID_CONFIG = GridConfig(
    entry_levels=1,
    starter_fraction=1.0,
    spacing_natr_multiplier=0.20,
    min_spacing_bps=4.0,
    max_spacing_bps=25.0,
    take_profit_steps=1.2,
    min_take_profit_bps=30.0,
)


def _parse_symbols(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(x.strip().upper() for x in text.split(",") if x.strip()))


def _require_validation_gate() -> dict:
    gate_path = Path(os.getenv("SIGNALGRID_SCALPING_GATE", "validation/scalping_gate.json"))
    if not gate_path.exists():
        raise RuntimeError(
            "SCALPING_QUANT_GATE_MISSING: run scalping validation before Binance Demo"
        )
    try:
        payload = json.loads(gate_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("SCALPING_QUANT_GATE_INVALID") from exc
    if payload.get("gate_passed") is not True:
        raise RuntimeError("SCALPING_QUANT_GATE_FAILED")
    if payload.get("profile_version") != SCALPING_PROFILE_VERSION:
        raise RuntimeError("SCALPING_QUANT_GATE_STALE_PROFILE")
    if payload.get("historical_microstructure_scope") != "PRICE_ACTION_TAKER_ONLY":
        raise RuntimeError("SCALPING_QUANT_GATE_SCOPE_INVALID")
    return payload


def _require_validated_symbols(
    gate_payload: dict,
    symbols: tuple[str, ...],
) -> None:
    validated_symbols = {
        str(symbol).upper()
        for symbol in gate_payload.get("validated_symbols", [])
        if str(symbol).strip()
    }
    requested_symbols = {symbol.upper() for symbol in symbols}
    unvalidated = sorted(requested_symbols - validated_symbols)
    if unvalidated:
        raise RuntimeError(
            "SCALPING_UNVALIDATED_SYMBOLS:" + ",".join(unvalidated)
        )


def build_scalping_demo_runtime(
    symbols: tuple[str, ...],
    db_path: str,
) -> SignalGridRuntime:
    gate_payload = _require_validation_gate()
    _require_validated_symbols(gate_payload, symbols)

    api_key = os.getenv("BINANCE_DEMO_API_KEY", "").strip()
    api_secret = os.getenv("BINANCE_DEMO_API_SECRET", "").strip()
    if not api_key or not api_secret:
        raise RuntimeError("BINANCE_DEMO_API_KEY and BINANCE_DEMO_API_SECRET are required")

    _configure_demo_compat(api_key, api_secret)
    cleanup = preflight_demo(
        _demo_rest_api(api_key, api_secret),
        symbols,
        leverage=SCALPING_RISK_CONFIG.leverage,
    )

    runtime = SignalGridRuntime.from_environment(
        RuntimeConfig(
            mode=RuntimeMode.TESTNET,
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
    )
    runtime.grid_config = SCALPING_GRID_CONFIG

    runtime.store.set_runtime("strategy_profile", SCALPING_PROFILE_VERSION)
    runtime.store.set_runtime("scalping_quant_gate", json.dumps(gate_payload, sort_keys=True))
    runtime.store.set_runtime("environment_label", "BINANCE_FUTURES_DEMO")
    runtime.store.set_runtime("demo_rest_url", DEMO_REST_URL)
    runtime.store.set_runtime("demo_ws_stream_url", DEMO_WS_STREAM_URL)
    runtime.store.set_runtime("demo_preflight_cleanup", json.dumps(cleanup, sort_keys=True))
    runtime.store.set_runtime(
        "scalping_signal_profile",
        json.dumps(
            {
                "structure_lookback": SCALPING_SIGNAL_CONFIG.structure_lookback,
                "compression_lookback": SCALPING_SIGNAL_CONFIG.compression_lookback,
                "compression_ratio_max": SCALPING_SIGNAL_CONFIG.compression_ratio_max,
                "max_spread_bps": SCALPING_SIGNAL_CONFIG.max_spread_bps,
                "min_expansion": SCALPING_SIGNAL_CONFIG.min_expansion,
                "min_directional_flow": SCALPING_SIGNAL_CONFIG.min_directional_flow,
                "min_score": SCALPING_SIGNAL_CONFIG.min_score,
                "ttl_seconds": SCALPING_SIGNAL_CONFIG.ttl_seconds,
            },
            sort_keys=True,
        ),
    )
    runtime.store.set_runtime(
        "scalping_execution_profile",
        json.dumps(
            {
                "entry_levels": SCALPING_GRID_CONFIG.entry_levels,
                "starter_fraction": SCALPING_GRID_CONFIG.starter_fraction,
                "min_spacing_bps": SCALPING_GRID_CONFIG.min_spacing_bps,
                "max_spacing_bps": SCALPING_GRID_CONFIG.max_spacing_bps,
                "take_profit_steps": SCALPING_GRID_CONFIG.take_profit_steps,
                "min_take_profit_bps": SCALPING_GRID_CONFIG.min_take_profit_bps,
                "max_positions": SCALPING_RISK_CONFIG.max_positions,
                "max_total_notional_usdt": SCALPING_RISK_CONFIG.max_total_notional_usdt,
                "leverage": SCALPING_RISK_CONFIG.leverage,
            },
            sort_keys=True,
        ),
    )
    return runtime


async def _run_with_progress(
    runtime: SignalGridRuntime,
    *,
    journal: Path,
    required_seconds: float,
    sample_interval_seconds: float,
):
    task = asyncio.create_task(
        run_soak_session(
            runtime,
            journal=journal,
            required_seconds=required_seconds,
            sample_interval_seconds=sample_interval_seconds,
            startup_timeout_seconds=120.0,
        ),
        name="signalgrid-scalping-demo",
    )
    try:
        while not task.done():
            await asyncio.sleep(min(5.0, sample_interval_seconds))
            if task.done():
                break
            print(
                json.dumps(
                    {
                        "scalping_progress": True,
                        "runtime": runtime.stats.snapshot(),
                        "last_open_error": json.loads(
                            runtime.store.get_runtime("last_open_error", "null") or "null"
                        ),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
        return await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SignalGrid Binance Futures Demo scalping V1")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SCALPING_SYMBOLS))
    parser.add_argument("--hours", type=float, default=1.0)
    parser.add_argument("--sample-seconds", type=float, default=15.0)
    parser.add_argument("--db", default="signalgrid-scalping-demo.db")
    parser.add_argument("--journal", default="scalping-demo.jsonl")
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
            parser.error("journal already contains samples; use --overwrite-journal or another path")
        journal.unlink()

    runtime = build_scalping_demo_runtime(symbols, args.db)
    try:
        report = asyncio.run(
            _run_with_progress(
                runtime,
                journal=journal,
                required_seconds=args.hours * 3600.0,
                sample_interval_seconds=args.sample_seconds,
            )
        )
        payload = {
            "strategy": "SCALPING_V1",
            "environment": "BINANCE_FUTURES_DEMO",
            "symbols": list(symbols),
            "runtime": runtime.stats.snapshot(),
            "soak": report.to_dict(),
            "last_open_error": json.loads(
                runtime.store.get_runtime("last_open_error", "null") or "null"
            ),
        }
        print(json.dumps(payload, indent=2 if args.pretty else None, sort_keys=True))
        return 0 if report.passed else 2
    finally:
        runtime.store.close()
        os.environ.pop("BINANCE_TESTNET_API_KEY", None)
        os.environ.pop("BINANCE_TESTNET_API_SECRET", None)


if __name__ == "__main__":
    raise SystemExit(main())
