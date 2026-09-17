from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any, Iterable

from signalgrid.engine import SignalGridEngine
from signalgrid.models import Direction
from signalgrid.ops.run_soak import run_soak_session
from signalgrid.risk.engine import RiskConfig, RiskEngine
from signalgrid.runtime import RuntimeConfig, RuntimeMode, SignalGridRuntime
from signalgrid.scanner import MultiSymbolScanner, ScanResult

# Binance Futures Demo Trading. REST URL is exposed by the official connector;
# the stream endpoint is the matching Demo Futures stream host.
DEMO_REST_URL = "https://demo-fapi.binance.com"
DEMO_WS_STREAM_URL = "wss://demo-fstream.binance.com"

DEFAULT_STRESS_SYMBOLS = (
    "BTCUSDT",
    "ETHUSDT",
    "UNIUSDT",
    "NEARUSDT",
    "ZECUSDT",
    "ONDOUSDT",
    "FETUSDT",
    "APTUSDT",
    "ARBUSDT",
    "AAVEUSDT",
    "ENAUSDT",
)

DEMO_RISK = RiskConfig(
    max_positions=6,
    max_total_notional_usdt=1_200.0,
    base_notional_usdt=100.0,
    max_notional_per_trade_usdt=200.0,
    leverage=3,
)


@dataclass(slots=True)
class ReflexStats:
    last_direction: dict[str, Direction] = field(default_factory=dict)
    direction_transitions: int = 0
    long_to_short_reversals: int = 0
    short_to_long_reversals: int = 0
    trade_to_pass_invalidations: int = 0
    pass_to_trade_activations: int = 0
    _transition_latency_ms: deque[float] = field(default_factory=lambda: deque(maxlen=2_000))

    def observe(self, result: ScanResult) -> None:
        symbol = result.symbol.upper()
        current = result.signal.direction
        previous = self.last_direction.get(symbol)
        if previous is not None and previous is not current:
            self.direction_transitions += 1
            self._transition_latency_ms.append(
                max(0.0, float(result.market_event_age_ms) + float(result.compute_latency_ms))
            )
            if previous is Direction.LONG and current is Direction.SHORT:
                self.long_to_short_reversals += 1
            elif previous is Direction.SHORT and current is Direction.LONG:
                self.short_to_long_reversals += 1
            elif previous in (Direction.LONG, Direction.SHORT) and current is Direction.PASS:
                self.trade_to_pass_invalidations += 1
            elif previous is Direction.PASS and current in (Direction.LONG, Direction.SHORT):
                self.pass_to_trade_activations += 1
        self.last_direction[symbol] = current

    def snapshot(self) -> dict[str, float | int | None]:
        values = sorted(self._transition_latency_ms)
        p95 = values[min(len(values) - 1, int(len(values) * 0.95))] if values else None
        return {
            "direction_transitions": self.direction_transitions,
            "long_to_short_reversals": self.long_to_short_reversals,
            "short_to_long_reversals": self.short_to_long_reversals,
            "trade_to_pass_invalidations": self.trade_to_pass_invalidations,
            "pass_to_trade_activations": self.pass_to_trade_activations,
            "transition_response_median_ms": median(values) if values else None,
            "transition_response_p95_ms": p95,
        }


class ReflexScanner(MultiSymbolScanner):
    def __init__(self, *args: Any, reflex_stats: ReflexStats | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.reflex_stats = reflex_stats or ReflexStats()

    def on_event(self, event):  # type: ignore[override]
        result = super().on_event(event)
        if result is not None:
            self.reflex_stats.observe(result)
        return result


def _unwrap_response(response: Any) -> Any:
    data = getattr(response, "data", None)
    return data() if callable(data) else response


def _dual_side_position(response: Any) -> bool | None:
    data = _unwrap_response(response)
    if isinstance(data, dict):
        value = data.get("dualSidePosition", data.get("dual_side_position"))
    else:
        value = getattr(data, "dual_side_position", None)
    return None if value is None else bool(value)


def preflight_demo(rest_api: Any, symbols: tuple[str, ...], leverage: int = 3) -> None:
    """Fail before trading if account mode is incompatible; pin symbol leverage."""
    position_mode = rest_api.get_current_position_mode()
    dual_side = _dual_side_position(position_mode)
    if dual_side is None:
        raise RuntimeError("Demo position mode could not be verified")
    if dual_side:
        raise RuntimeError("SignalGrid requires Binance One-way Mode; Hedge Mode is enabled")

    for symbol in symbols:
        rest_api.change_initial_leverage(symbol=symbol, leverage=leverage)


def _parse_symbols(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(x.strip().upper() for x in text.split(",") if x.strip()))


def _configure_demo_compat(api_key: str, api_secret: str) -> None:
    # SignalGrid's authenticated runtime already has the desired fail-closed
    # TESTNET lifecycle. Route that lifecycle to Binance Futures Demo endpoints
    # without touching production credentials or endpoints.
    import binance_common.constants as constants

    constants.DERIVATIVES_TRADING_USDS_FUTURES_REST_API_TESTNET_URL = DEMO_REST_URL
    constants.DERIVATIVES_TRADING_USDS_FUTURES_WS_STREAMS_TESTNET_URL = DEMO_WS_STREAM_URL
    os.environ["BINANCE_TESTNET_API_KEY"] = api_key
    os.environ["BINANCE_TESTNET_API_SECRET"] = api_secret


def _demo_rest_api(api_key: str, api_secret: str) -> Any:
    from binance_common.configuration import ConfigurationRestAPI
    from binance_sdk_derivatives_trading_usds_futures.derivatives_trading_usds_futures import (
        DerivativesTradingUsdsFutures,
    )

    cfg = ConfigurationRestAPI(api_key=api_key, api_secret=api_secret, base_path=DEMO_REST_URL)
    return DerivativesTradingUsdsFutures(config_rest_api=cfg).rest_api


def build_demo_runtime(symbols: tuple[str, ...], db_path: str) -> tuple[SignalGridRuntime, ReflexStats]:
    api_key = os.getenv("BINANCE_DEMO_API_KEY", "").strip()
    api_secret = os.getenv("BINANCE_DEMO_API_SECRET", "").strip()
    if not api_key or not api_secret:
        raise RuntimeError("BINANCE_DEMO_API_KEY and BINANCE_DEMO_API_SECRET are required")

    _configure_demo_compat(api_key, api_secret)
    preflight_demo(_demo_rest_api(api_key, api_secret), symbols, leverage=DEMO_RISK.leverage)

    runtime = SignalGridRuntime.from_environment(
        RuntimeConfig(mode=RuntimeMode.TESTNET, symbols=symbols, db_path=db_path)
    )
    base = runtime.scanner
    reflex = ReflexStats()
    runtime.scanner = ReflexScanner(
        runtime.router,
        SignalGridEngine(risk_engine=RiskEngine(DEMO_RISK)),
        base.config,
        positions_provider=base.positions_provider,
        now_ms=base.now_ms,
        reflex_stats=reflex,
    )
    runtime.store.set_runtime("environment_label", "BINANCE_FUTURES_DEMO")
    runtime.store.set_runtime("demo_rest_url", DEMO_REST_URL)
    runtime.store.set_runtime("demo_ws_stream_url", DEMO_WS_STREAM_URL)
    return runtime, reflex


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SignalGrid Binance Futures Demo reflex stress test")
    parser.add_argument("--symbols", default=",".join(DEFAULT_STRESS_SYMBOLS))
    parser.add_argument("--hours", type=float, default=1.0)
    parser.add_argument("--sample-seconds", type=float, default=30.0)
    parser.add_argument("--db", default="signalgrid-demo.db")
    parser.add_argument("--journal", default="demo-reflex.jsonl")
    parser.add_argument("--overwrite-journal", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.hours <= 0:
        parser.error("--hours must be positive")
    symbols = _parse_symbols(args.symbols)
    if not symbols:
        parser.error("at least one symbol is required")

    journal = Path(args.journal)
    if journal.exists() and journal.stat().st_size > 0:
        if not args.overwrite_journal:
            parser.error("journal already contains samples; use --overwrite-journal or another path")
        journal.unlink()

    runtime, reflex = build_demo_runtime(symbols, args.db)
    try:
        report = asyncio.run(
            run_soak_session(
                runtime,
                journal=journal,
                required_seconds=args.hours * 3600.0,
                sample_interval_seconds=args.sample_seconds,
                startup_timeout_seconds=120.0,
            )
        )
        payload = {
            "environment": "BINANCE_FUTURES_DEMO",
            "symbols": list(symbols),
            "risk": {
                "max_positions": DEMO_RISK.max_positions,
                "max_total_notional_usdt": DEMO_RISK.max_total_notional_usdt,
                "base_notional_usdt": DEMO_RISK.base_notional_usdt,
                "max_notional_per_trade_usdt": DEMO_RISK.max_notional_per_trade_usdt,
                "leverage": DEMO_RISK.leverage,
            },
            "soak": report.to_dict(),
            "runtime": runtime.stats.snapshot(),
            "reflex": reflex.snapshot(),
        }
        print(json.dumps(payload, indent=2 if args.pretty else None, sort_keys=True))
        return 0 if report.passed else 2
    finally:
        runtime.store.close()
        os.environ.pop("BINANCE_TESTNET_API_KEY", None)
        os.environ.pop("BINANCE_TESTNET_API_SECRET", None)


if __name__ == "__main__":
    raise SystemExit(main())
