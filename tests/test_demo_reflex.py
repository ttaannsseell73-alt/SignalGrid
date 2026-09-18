from __future__ import annotations

from types import SimpleNamespace

import pytest

from signalgrid.models import Direction
from signalgrid.ops.run_demo_reflex import DEMO_RISK, DEMO_SIGNAL_CONFIG, ReflexStats, preflight_demo
from signalgrid.signals.engine import SignalConfig


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def data(self):
        return self._data


class FakeRest:
    def __init__(self, dual_side: bool):
        self.dual_side = dual_side
        self.leverage_calls: list[tuple[str, int]] = []

    def get_current_position_mode(self):
        return FakeResponse(SimpleNamespace(dual_side_position=self.dual_side))

    def change_initial_leverage(self, *, symbol: str, leverage: int):
        self.leverage_calls.append((symbol, leverage))
        return FakeResponse({"symbol": symbol, "leverage": leverage})


def _result(symbol: str, direction: Direction, age: int = 4, compute: float = 1.5, *, setup: str | None = None, emitted: bool | None = None, decision_reason: str | None = None):
    if setup is None:
        setup = "NO_STRUCTURE" if direction is Direction.PASS else "BREAKOUT_ACCEPTANCE"
    if emitted is None:
        emitted = direction is not Direction.PASS
    approved = direction is not Direction.PASS and bool(emitted)
    if decision_reason is None:
        decision_reason = "APPROVED" if approved else ("NO_VALID_SIGNAL" if direction is Direction.PASS else "SIGNAL_DEBOUNCE")
    return SimpleNamespace(
        symbol=symbol,
        signal=SimpleNamespace(direction=direction, setup=setup),
        decision=SimpleNamespace(approved=approved, reason=decision_reason),
        emitted=bool(emitted),
        market_event_age_ms=age,
        compute_latency_ms=compute,
    )


def test_demo_preflight_requires_one_way_mode():
    rest = FakeRest(dual_side=True)
    with pytest.raises(RuntimeError, match="One-way Mode"):
        preflight_demo(rest, ("BTCUSDT",), leverage=3)
    assert rest.leverage_calls == []


def test_demo_preflight_pins_three_x_leverage_for_every_symbol():
    rest = FakeRest(dual_side=False)
    symbols = ("BTCUSDT", "ETHUSDT", "UNIUSDT")
    preflight_demo(rest, symbols, leverage=DEMO_RISK.leverage)
    assert rest.leverage_calls == [(symbol, 3) for symbol in symbols]


def test_reflex_stats_count_direction_changes_and_latency():
    stats = ReflexStats()
    stats.observe(_result("UNIUSDT", Direction.PASS))
    stats.observe(_result("UNIUSDT", Direction.LONG, age=5, compute=2.0))
    stats.observe(_result("UNIUSDT", Direction.PASS, age=6, compute=1.0))
    stats.observe(_result("UNIUSDT", Direction.SHORT, age=7, compute=1.0))
    stats.observe(_result("UNIUSDT", Direction.LONG, age=8, compute=2.0))

    snap = stats.snapshot()
    assert snap["direction_transitions"] == 4
    assert snap["pass_to_trade_activations"] == 2
    assert snap["trade_to_pass_invalidations"] == 1
    assert snap["short_to_long_reversals"] == 1
    assert snap["long_to_short_reversals"] == 0
    assert snap["transition_response_median_ms"] == 7.5
    assert snap["transition_response_p95_ms"] == 10.0


def test_demo_signal_profile_is_reactive_without_changing_production_defaults():
    prod = SignalConfig()
    assert DEMO_SIGNAL_CONFIG.structure_lookback == 5
    assert DEMO_SIGNAL_CONFIG.structure_lookback < prod.structure_lookback
    assert DEMO_SIGNAL_CONFIG.min_expansion < prod.min_expansion
    assert DEMO_SIGNAL_CONFIG.entry_threshold < prod.entry_threshold
    assert prod.structure_lookback == 20
    assert prod.min_expansion == 1.05
    assert prod.entry_threshold == 0.60


def test_reflex_stats_records_signal_gate_rejections_and_emissions():
    stats = ReflexStats()
    stats.observe(_result("UNIUSDT", Direction.PASS, setup="NO_STRUCTURE"))
    stats.observe(_result("UNIUSDT", Direction.PASS, setup="NO_VOL_EXPANSION"))
    stats.observe(_result("UNIUSDT", Direction.LONG, emitted=False, decision_reason="SIGNAL_DEBOUNCE"))
    stats.observe(_result("UNIUSDT", Direction.LONG, emitted=True))
    stats.observe(_result("NEARUSDT", Direction.SHORT, emitted=True))

    snap = stats.snapshot()
    assert snap["evaluations_seen"] == 5
    assert snap["pass_signals_seen"] == 2
    assert snap["long_signals_seen"] == 2
    assert snap["short_signals_seen"] == 1
    assert snap["emitted_long"] == 1
    assert snap["emitted_short"] == 1
    assert snap["rejection_reasons"]["NO_STRUCTURE"] == 1
    assert snap["rejection_reasons"]["NO_VOL_EXPANSION"] == 1
    assert snap["rejection_reasons"]["SIGNAL_DEBOUNCE"] == 1
