from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from time import sleep, time
from typing import Any, Iterable

from signalgrid.engine import SignalGridEngine
from signalgrid.execution.binance import client_order_id
from signalgrid.models import Direction, GridMode
from signalgrid.ops.run_soak import run_soak_session
from signalgrid.ops.signal_diagnostics import SignalDiagnosticCounter
from signalgrid.risk.engine import RiskConfig, RiskEngine
from signalgrid.signals.engine import SignalConfig, SignalEngine
from signalgrid.runtime import RuntimeConfig, RuntimeMode, SignalGridRuntime
from signalgrid.scanner import MultiSymbolScanner, ScanResult
from signalgrid.state.reconciliation import BinanceRestSnapshotProvider

# Binance Futures Demo Trading. REST URL is exposed by the official connector;
# the stream endpoint is the matching Demo Futures stream host.
DEMO_REST_URL = "https://demo-fapi.binance.com"
DEMO_WS_STREAM_URL = "wss://demo-fstream.binance.com"
DEMO_REST_TIMEOUT_MS = 5_000
DEMO_REST_RETRIES = 3
DEMO_REST_BACKOFF_MS = 500

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

# Deliberately more reactive than the production profile. This profile exists
# only to exercise signal -> risk -> Binance Demo execution/reflex behavior.
DEMO_SIGNAL_CONFIG = SignalConfig(
    structure_lookback=5,
    max_spread_bps=10.0,
    min_expansion=0.90,
    strong_expansion=1.20,
    min_flow_abs=0.05,
    min_book_abs=0.03,
    entry_threshold=0.52,
    min_directional_stop_bps=3.0,
    neutral_enabled=True,
    neutral_max_expansion=1.00,
    neutral_max_flow_abs=0.15,
    neutral_max_book_abs=0.15,
    neutral_entry_threshold=0.52,
    ttl_seconds=20.0,
)


@dataclass(slots=True)
class ReflexStats:
    last_direction: dict[str, Direction] = field(default_factory=dict)
    direction_transitions: int = 0
    long_to_short_reversals: int = 0
    short_to_long_reversals: int = 0
    trade_to_pass_invalidations: int = 0
    pass_to_trade_activations: int = 0
    evaluations_seen: int = 0
    long_signals_seen: int = 0
    short_signals_seen: int = 0
    pass_signals_seen: int = 0
    emitted_long: int = 0
    emitted_short: int = 0
    neutral_signals_seen: int = 0
    emitted_neutral: int = 0
    last_mode: dict[str, GridMode] = field(default_factory=dict)
    mode_transitions: int = 0
    diagnostics: SignalDiagnosticCounter = field(default_factory=SignalDiagnosticCounter)
    _transition_latency_ms: deque[float] = field(default_factory=lambda: deque(maxlen=2_000))

    def observe(self, result: ScanResult) -> None:
        symbol = result.symbol.upper()
        current = result.signal.direction
        self.evaluations_seen += 1
        mode = getattr(result.signal, "grid_mode", None) or GridMode.PASS
        if mode is GridMode.NEUTRAL_GRID:
            self.neutral_signals_seen += 1
            if result.emitted:
                self.emitted_neutral += 1
            elif not result.decision.approved:
                self.diagnostics.observe(result.decision.reason)
        elif current is Direction.PASS:
            self.pass_signals_seen += 1
            self.diagnostics.observe(getattr(result.signal, "setup", "PASS"))
        elif current is Direction.LONG:
            self.long_signals_seen += 1
            if result.emitted:
                self.emitted_long += 1
            elif not result.decision.approved:
                self.diagnostics.observe(result.decision.reason)
        elif current is Direction.SHORT:
            self.short_signals_seen += 1
            if result.emitted:
                self.emitted_short += 1
            elif not result.decision.approved:
                self.diagnostics.observe(result.decision.reason)

        previous_mode = self.last_mode.get(symbol)
        if previous_mode is not None and previous_mode is not mode:
            self.mode_transitions += 1
        self.last_mode[symbol] = mode

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

    def snapshot(self) -> dict[str, Any]:
        values = sorted(self._transition_latency_ms)
        p95 = values[min(len(values) - 1, int(len(values) * 0.95))] if values else None
        return {
            "direction_transitions": self.direction_transitions,
            "long_to_short_reversals": self.long_to_short_reversals,
            "short_to_long_reversals": self.short_to_long_reversals,
            "trade_to_pass_invalidations": self.trade_to_pass_invalidations,
            "pass_to_trade_activations": self.pass_to_trade_activations,
            "evaluations_seen": self.evaluations_seen,
            "long_signals_seen": self.long_signals_seen,
            "short_signals_seen": self.short_signals_seen,
            "pass_signals_seen": self.pass_signals_seen,
            "emitted_long": self.emitted_long,
            "emitted_short": self.emitted_short,
            "neutral_signals_seen": self.neutral_signals_seen,
            "emitted_neutral": self.emitted_neutral,
            "mode_transitions": self.mode_transitions,
            "rejection_reasons": self.diagnostics.snapshot(),
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


def _is_transient_network_error(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    return (
        "network" in name
        or "timeout" in name
        or "read timed out" in text
        or "connection" in text
        or "temporarily unavailable" in text
    )


def _call_idempotent_with_retry(call, *args, attempts: int = 3, **kwargs):
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return call(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if not _is_transient_network_error(exc) or attempt + 1 >= attempts:
                raise
            sleep(0.5 * (2 ** attempt))
    assert last_exc is not None
    raise last_exc


def _is_owned_client_id(value: str) -> bool:
    return bool(value) and value.startswith("sg-")


def _is_missing_order_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "-2011" in text or "unknown order sent" in text


def _reset_stale_demo_state(rest_api: Any, allowed_symbols: tuple[str, ...]) -> dict[str, int]:
    """Reset only the dedicated Demo test universe before a fresh run.

    Safety rules:
      - never touch non-SignalGrid open orders/algos;
      - never close positions outside the configured stress universe;
      - cancel stale SignalGrid entry orders first so they cannot re-open exposure;
      - close remaining Demo positions with reduce-only MARKET orders;
      - keep protective algos until positions are flat, then cancel them;
      - verify the account is actually clean before reconciliation starts.
    """
    provider = BinanceRestSnapshotProvider(rest_api)
    snapshot = provider.snapshot()
    allowed = {symbol.upper() for symbol in allowed_symbols}

    foreign_orders = [
        o.client_order_id or o.exchange_order_id
        for o in snapshot.orders
        if not _is_owned_client_id(o.client_order_id)
    ]
    foreign_algos = [
        a.client_algo_id or a.algo_id
        for a in snapshot.algo_orders
        if not _is_owned_client_id(a.client_algo_id)
    ]
    if foreign_orders or foreign_algos:
        foreign = ",".join(foreign_orders + foreign_algos)
        raise RuntimeError(f"Demo preflight found non-SignalGrid open order(s): {foreign}")

    outside_positions = [p.symbol for p in snapshot.positions if p.symbol not in allowed]
    if outside_positions:
        raise RuntimeError(
            "Demo preflight found position(s) outside the configured SignalGrid universe: "
            + ",".join(sorted(outside_positions))
        )

    canceled_orders = 0
    canceled_algos = 0
    closed_positions = 0

    # Remove stale grid-entry orders first. Protective algos remain in place
    # until the corresponding position has been flattened.
    for order in snapshot.orders:
        try:
            rest_api.cancel_order(symbol=order.symbol, order_id=int(order.exchange_order_id))
            canceled_orders += 1
        except Exception as exc:
            if not _is_missing_order_error(exc):
                raise

    for position in snapshot.positions:
        side = "SELL" if position.direction == "LONG" else "BUY"
        cid = client_order_id(
            "r",
            f"demo-reset:{position.symbol}:{position.direction}:{position.quantity}:{int(time() * 1000)}",
        )
        rest_api.new_order(
            symbol=position.symbol,
            side=side,
            type="MARKET",
            quantity=float(position.quantity),
            reduce_only="true",
            new_client_order_id=cid,
            new_order_resp_type="RESULT",
        )
        closed_positions += 1

    if snapshot.positions:
        for _ in range(20):
            sleep(0.25)
            current = provider.snapshot()
            if not current.positions:
                snapshot = current
                break
        else:
            symbols = ",".join(p.symbol for p in current.positions)
            raise RuntimeError(f"Demo preflight could not flatten stale position(s): {symbols}")
    else:
        snapshot = provider.snapshot()

    # Position is now flat, so sibling protective algos can be safely removed.
    for algo in snapshot.algo_orders:
        try:
            rest_api.cancel_algo_order(client_algo_id=algo.client_algo_id)
            canceled_algos += 1
        except Exception as exc:
            if not _is_missing_order_error(exc):
                raise

    for _ in range(20):
        sleep(0.25)
        remaining = provider.snapshot()
        if not remaining.positions and not remaining.orders and not remaining.algo_orders:
            break
    else:
        remaining_ids = [o.client_order_id or o.exchange_order_id for o in remaining.orders]
        remaining_ids += [a.client_algo_id or a.algo_id for a in remaining.algo_orders]
        remaining_ids += [p.symbol for p in remaining.positions]
        raise RuntimeError(
            "Demo preflight could not fully reset stale SignalGrid state: "
            + ",".join(remaining_ids)
        )

    return {
        "canceled_orders": canceled_orders,
        "canceled_algos": canceled_algos,
        "closed_positions": closed_positions,
    }


def preflight_demo(rest_api: Any, symbols: tuple[str, ...], leverage: int = 3) -> dict[str, int]:
    """Verify account mode, reset stale Demo test state, then pin leverage."""
    position_mode = _call_idempotent_with_retry(rest_api.get_current_position_mode)
    dual_side = _dual_side_position(position_mode)
    if dual_side is None:
        raise RuntimeError("Demo position mode could not be verified")
    if dual_side:
        raise RuntimeError("SignalGrid requires Binance One-way Mode; Hedge Mode is enabled")

    cleanup = _reset_stale_demo_state(rest_api, symbols)
    for symbol in symbols:
        _call_idempotent_with_retry(
            rest_api.change_initial_leverage,
            symbol=symbol,
            leverage=leverage,
        )
    return cleanup


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

    cfg = ConfigurationRestAPI(
        api_key=api_key,
        api_secret=api_secret,
        base_path=DEMO_REST_URL,
        timeout=DEMO_REST_TIMEOUT_MS,
        retries=DEMO_REST_RETRIES,
        backoff=DEMO_REST_BACKOFF_MS,
    )
    return DerivativesTradingUsdsFutures(config_rest_api=cfg).rest_api


def build_demo_runtime(symbols: tuple[str, ...], db_path: str) -> tuple[SignalGridRuntime, ReflexStats]:
    api_key = os.getenv("BINANCE_DEMO_API_KEY", "").strip()
    api_secret = os.getenv("BINANCE_DEMO_API_SECRET", "").strip()
    if not api_key or not api_secret:
        raise RuntimeError("BINANCE_DEMO_API_KEY and BINANCE_DEMO_API_SECRET are required")

    _configure_demo_compat(api_key, api_secret)
    cleanup = preflight_demo(_demo_rest_api(api_key, api_secret), symbols, leverage=DEMO_RISK.leverage)

    runtime = SignalGridRuntime.from_environment(
        RuntimeConfig(mode=RuntimeMode.TESTNET, symbols=symbols, db_path=db_path)
    )
    base = runtime.scanner
    reflex = ReflexStats()
    runtime.scanner = ReflexScanner(
        runtime.router,
        SignalGridEngine(
            signal_engine=SignalEngine(DEMO_SIGNAL_CONFIG),
            risk_engine=RiskEngine(DEMO_RISK),
        ),
        base.config,
        positions_provider=base.positions_provider,
        now_ms=base.now_ms,
        reflex_stats=reflex,
    )
    runtime.store.set_runtime("environment_label", "BINANCE_FUTURES_DEMO")
    runtime.store.set_runtime("demo_rest_url", DEMO_REST_URL)
    runtime.store.set_runtime("demo_ws_stream_url", DEMO_WS_STREAM_URL)
    runtime.store.set_runtime("demo_preflight_cleanup", json.dumps(cleanup, sort_keys=True))
    runtime.store.set_runtime(
        "demo_signal_profile",
        json.dumps({
            "structure_lookback": DEMO_SIGNAL_CONFIG.structure_lookback,
            "max_spread_bps": DEMO_SIGNAL_CONFIG.max_spread_bps,
            "min_expansion": DEMO_SIGNAL_CONFIG.min_expansion,
            "strong_expansion": DEMO_SIGNAL_CONFIG.strong_expansion,
            "min_flow_abs": DEMO_SIGNAL_CONFIG.min_flow_abs,
            "min_book_abs": DEMO_SIGNAL_CONFIG.min_book_abs,
            "entry_threshold": DEMO_SIGNAL_CONFIG.entry_threshold,
            "min_directional_stop_bps": DEMO_SIGNAL_CONFIG.min_directional_stop_bps,
        }, sort_keys=True),
    )
    return runtime, reflex


async def _run_demo_with_progress(
    runtime: SignalGridRuntime,
    reflex: ReflexStats,
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
        name="signalgrid-demo-soak",
    )
    try:
        while not task.done():
            await asyncio.sleep(min(10.0, sample_interval_seconds))
            if task.done():
                break
            print(
                json.dumps(
                    {
                        "demo_progress": True,
                        "runtime": runtime.stats.snapshot(),
                        "reflex": reflex.snapshot(),
                        "last_open_error": json.loads(runtime.store.get_runtime("last_open_error", "null") or "null"),
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
            _run_demo_with_progress(
                runtime,
                reflex,
                journal=journal,
                required_seconds=args.hours * 3600.0,
                sample_interval_seconds=args.sample_seconds,
            )
        )
        payload = {
            "environment": "BINANCE_FUTURES_DEMO",
            "symbols": list(symbols),
            "signal_profile": {
                "structure_lookback": DEMO_SIGNAL_CONFIG.structure_lookback,
                "max_spread_bps": DEMO_SIGNAL_CONFIG.max_spread_bps,
                "min_expansion": DEMO_SIGNAL_CONFIG.min_expansion,
                "strong_expansion": DEMO_SIGNAL_CONFIG.strong_expansion,
                "min_flow_abs": DEMO_SIGNAL_CONFIG.min_flow_abs,
                "min_book_abs": DEMO_SIGNAL_CONFIG.min_book_abs,
                "entry_threshold": DEMO_SIGNAL_CONFIG.entry_threshold,
                "min_directional_stop_bps": DEMO_SIGNAL_CONFIG.min_directional_stop_bps,
                "neutral_enabled": DEMO_SIGNAL_CONFIG.neutral_enabled,
                "neutral_max_expansion": DEMO_SIGNAL_CONFIG.neutral_max_expansion,
                "neutral_max_flow_abs": DEMO_SIGNAL_CONFIG.neutral_max_flow_abs,
                "neutral_max_book_abs": DEMO_SIGNAL_CONFIG.neutral_max_book_abs,
                "neutral_entry_threshold": DEMO_SIGNAL_CONFIG.neutral_entry_threshold,
            },
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
            "last_open_error": json.loads(runtime.store.get_runtime("last_open_error", "null") or "null"),
        }
        print(json.dumps(payload, indent=2 if args.pretty else None, sort_keys=True))
        return 0 if report.passed else 2
    finally:
        runtime.store.close()
        os.environ.pop("BINANCE_TESTNET_API_KEY", None)
        os.environ.pop("BINANCE_TESTNET_API_SECRET", None)


if __name__ == "__main__":
    raise SystemExit(main())
