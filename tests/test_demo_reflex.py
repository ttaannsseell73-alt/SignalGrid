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
    def __init__(self, dual_side: bool, *, orders=None, algos=None, positions=None, leverage_failures: int = 0):
        self.dual_side = dual_side
        self.leverage_failures = leverage_failures
        self.leverage_calls: list[tuple[str, int]] = []
        self.orders = list(orders or [])
        self.algos = list(algos or [])
        self.positions = list(positions or [])
        self.cancel_order_calls = []
        self.cancel_algo_calls = []
        self.new_order_calls = []

    def get_current_position_mode(self):
        return FakeResponse(SimpleNamespace(dual_side_position=self.dual_side))

    def change_initial_leverage(self, *, symbol: str, leverage: int):
        self.leverage_calls.append((symbol, leverage))
        if self.leverage_failures > 0:
            self.leverage_failures -= 1
            exc = RuntimeError("Network error: read timed out")
            exc.__class__.__name__ = "RuntimeError"
            raise exc
        return FakeResponse({"symbol": symbol, "leverage": leverage})

    def current_all_open_orders(self):
        return FakeResponse(list(self.orders))

    def current_all_algo_open_orders(self):
        return FakeResponse(list(self.algos))

    def position_information_v3(self):
        return FakeResponse(list(self.positions))

    def new_order(self, **kwargs):
        self.new_order_calls.append(kwargs)
        symbol = kwargs["symbol"]
        side = kwargs["side"]
        if kwargs.get("reduce_only") == "true" and kwargs.get("type") == "MARKET":
            remaining = []
            for item in self.positions:
                if item["symbol"] != symbol:
                    remaining.append(item)
                    continue
                amt = float(item["positionAmt"])
                if (amt > 0 and side == "SELL") or (amt < 0 and side == "BUY"):
                    continue
                remaining.append(item)
            self.positions = remaining
        return FakeResponse({"orderId": 999})

    def cancel_order(self, *, symbol: str, order_id: int):
        self.cancel_order_calls.append((symbol, order_id))
        self.orders = [x for x in self.orders if int(x["orderId"]) != int(order_id)]

    def cancel_algo_order(self, *, client_algo_id: str):
        self.cancel_algo_calls.append(client_algo_id)
        self.algos = [x for x in self.algos if x["clientAlgoId"] != client_algo_id]


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


def test_demo_preflight_cleans_stale_owned_signalgrid_orders_before_leverage():
    rest = FakeRest(
        dual_side=False,
        orders=[
            {
                "clientOrderId": "sg-g-abc",
                "orderId": 101,
                "symbol": "BTCUSDT",
                "side": "BUY",
                "status": "NEW",
                "type": "LIMIT",
                "origQty": "0.01",
                "executedQty": "0",
                "avgPrice": "0",
                "reduceOnly": False,
            }
        ],
        algos=[
            {
                "clientAlgoId": "sg-x-def",
                "algoId": 202,
                "symbol": "BTCUSDT",
                "side": "SELL",
                "algoStatus": "NEW",
                "algoType": "CONDITIONAL",
                "orderType": "STOP_MARKET",
                "triggerPrice": "100",
                "quantity": "0",
                "closePosition": True,
                "reduceOnly": False,
                "actualOrderId": "",
            }
        ],
    )
    result = preflight_demo(rest, ("BTCUSDT",), leverage=3)
    assert result == {"canceled_orders": 1, "canceled_algos": 1, "closed_positions": 0}
    assert rest.cancel_order_calls == [("BTCUSDT", 101)]
    assert rest.cancel_algo_calls == ["sg-x-def"]
    assert rest.leverage_calls == [("BTCUSDT", 3)]


def test_demo_preflight_refuses_foreign_open_order():
    rest = FakeRest(
        dual_side=False,
        orders=[
            {
                "clientOrderId": "manual-order",
                "orderId": 303,
                "symbol": "BTCUSDT",
                "side": "BUY",
                "status": "NEW",
                "type": "LIMIT",
                "origQty": "0.01",
                "executedQty": "0",
                "avgPrice": "0",
                "reduceOnly": False,
            }
        ],
    )
    with pytest.raises(RuntimeError, match="non-SignalGrid"):
        preflight_demo(rest, ("BTCUSDT",), leverage=3)
    assert rest.cancel_order_calls == []
    assert rest.leverage_calls == []


def test_demo_preflight_flattens_stale_position_in_configured_universe():
    rest = FakeRest(
        dual_side=False,
        positions=[
            {
                "symbol": "BTCUSDT",
                "positionAmt": "0.01",
                "positionSide": "BOTH",
                "entryPrice": "100000",
            }
        ],
    )
    result = preflight_demo(rest, ("BTCUSDT",), leverage=3)
    assert result["closed_positions"] == 1
    assert len(rest.new_order_calls) == 1
    close = rest.new_order_calls[0]
    assert close["symbol"] == "BTCUSDT"
    assert close["side"] == "SELL"
    assert close["type"] == "MARKET"
    assert close["reduce_only"] == "true"
    assert close["quantity"] == 0.01
    assert rest.positions == []
    assert rest.leverage_calls == [("BTCUSDT", 3)]


def test_demo_preflight_refuses_position_outside_configured_universe():
    rest = FakeRest(
        dual_side=False,
        positions=[
            {
                "symbol": "DOGEUSDT",
                "positionAmt": "-5",
                "positionSide": "BOTH",
                "entryPrice": "0.2",
            }
        ],
    )
    with pytest.raises(RuntimeError, match="outside the configured SignalGrid universe"):
        preflight_demo(rest, ("BTCUSDT",), leverage=3)
    assert rest.new_order_calls == []
    assert rest.leverage_calls == []


def test_demo_preflight_keeps_protective_algo_until_position_is_flat():
    rest = FakeRest(
        dual_side=False,
        positions=[
            {
                "symbol": "BTCUSDT",
                "positionAmt": "-0.02",
                "positionSide": "BOTH",
                "entryPrice": "100000",
            }
        ],
        orders=[
            {
                "clientOrderId": "sg-g-entry",
                "orderId": 111,
                "symbol": "BTCUSDT",
                "side": "SELL",
                "status": "NEW",
                "type": "LIMIT",
                "origQty": "0.01",
                "executedQty": "0",
                "avgPrice": "0",
                "reduceOnly": False,
            }
        ],
        algos=[
            {
                "clientAlgoId": "sg-x-protect",
                "algoId": 222,
                "symbol": "BTCUSDT",
                "side": "BUY",
                "algoStatus": "NEW",
                "algoType": "CONDITIONAL",
                "orderType": "STOP_MARKET",
                "triggerPrice": "101000",
                "quantity": "0",
                "closePosition": True,
                "reduceOnly": False,
                "actualOrderId": "",
            }
        ],
    )
    result = preflight_demo(rest, ("BTCUSDT",), leverage=3)
    assert result == {"canceled_orders": 1, "canceled_algos": 1, "closed_positions": 1}
    assert rest.cancel_order_calls == [("BTCUSDT", 111)]
    assert rest.new_order_calls[0]["side"] == "BUY"
    assert rest.cancel_algo_calls == ["sg-x-protect"]
    assert rest.positions == []
    assert rest.orders == []
    assert rest.algos == []


def test_demo_preflight_retries_transient_leverage_timeout():
    rest = FakeRest(dual_side=False, leverage_failures=1)
    result = preflight_demo(rest, ("BTCUSDT",), leverage=3)
    assert result == {"canceled_orders": 0, "canceled_algos": 0, "closed_positions": 0}
    assert rest.leverage_calls == [("BTCUSDT", 3), ("BTCUSDT", 3)]
