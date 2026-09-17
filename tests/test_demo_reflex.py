from __future__ import annotations

from types import SimpleNamespace

import pytest

from signalgrid.models import Direction
from signalgrid.ops.run_demo_reflex import DEMO_RISK, ReflexStats, preflight_demo


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


def _result(symbol: str, direction: Direction, age: int = 4, compute: float = 1.5):
    return SimpleNamespace(
        symbol=symbol,
        signal=SimpleNamespace(direction=direction),
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
    assert snap["transition_response_median_ms"] == 7.0
    assert snap["transition_response_p95_ms"] == 10.0
