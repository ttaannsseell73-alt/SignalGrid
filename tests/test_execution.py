import asyncio

from signalgrid.execution.binance import (
    BinanceRestExecutionAdapter,
    ExecutionBlockedError,
    InvalidIntentError,
    OrderIntent,
    ProtectiveExitIntent,
    client_order_id,
)
from signalgrid.models import Direction

EXCHANGE_INFO = {
    "symbols": [{
        "symbol": "SOLUSDT",
        "filters": [
            {"filterType": "LOT_SIZE", "stepSize": "0.1", "minQty": "0.1"},
            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
            {"filterType": "MIN_NOTIONAL", "notional": "5"},
        ],
    }]
}

class FakeRest:
    def exchange_information(self): return EXCHANGE_INFO
    def new_order(self, **kwargs): self.order = kwargs; return {"orderId": 123}
    def new_algo_order(self, **kwargs): self.algo = kwargs; return {"clientAlgoId": kwargs["client_algo_id"]}
    def cancel_order(self, **kwargs): self.cancel = kwargs

def test_entry_rounding_and_idempotent_client_id():
    rest = FakeRest(); adapter = BinanceRestExecutionAdapter(rest, lambda symbol: 143.27, execution_gate=lambda: True)
    intent = OrderIntent("SOLUSDT", Direction.LONG, 100.0, 3, 140.0, "sig-1")
    receipt = asyncio.run(adapter.place_entry_receipt(intent))
    assert str(receipt.quantity) == "0.6"
    assert rest.order["new_client_order_id"] == client_order_id("e", "sig-1")
    assert asyncio.run(adapter.place_entry(intent)) == client_order_id("e", "sig-1")

def test_protective_stop_uses_algo_order_and_tick_rounding():
    rest = FakeRest(); adapter = BinanceRestExecutionAdapter(rest, lambda symbol: 143.27, execution_gate=lambda: True)
    result = asyncio.run(adapter.place_protective_exit(ProtectiveExitIntent("SOLUSDT", Direction.LONG, 140.037, "sig-1-stop")))
    assert result == client_order_id("x", "sig-1-stop")
    assert rest.algo["type"] == "STOP_MARKET"
    assert rest.algo["trigger_price"] == 140.03
    assert rest.algo["side"] == "SELL"

def test_invalid_direction_is_rejected_before_exchange_call():
    rest = FakeRest(); adapter = BinanceRestExecutionAdapter(rest, lambda symbol: 143.27, execution_gate=lambda: True)
    intent = OrderIntent("SOLUSDT", Direction.PASS, 100.0, 3, None, "sig-pass")
    try: asyncio.run(adapter.place_entry(intent))
    except InvalidIntentError: pass
    else: raise AssertionError("PASS intent must not reach execution")


def test_entry_is_blocked_when_reconciliation_gate_is_closed():
    rest = FakeRest()
    adapter = BinanceRestExecutionAdapter(rest, lambda symbol: 143.27, execution_gate=lambda: False)
    intent = OrderIntent("SOLUSDT", Direction.LONG, 100.0, 3, 140.0, "sig-blocked")
    try:
        asyncio.run(adapter.place_entry(intent))
    except ExecutionBlockedError:
        pass
    else:
        raise AssertionError("closed execution gate must block new entry")
    assert not hasattr(rest, "order")


def test_entry_is_safe_by_default_without_execution_gate():
    rest = FakeRest()
    adapter = BinanceRestExecutionAdapter(rest, lambda symbol: 143.27)
    intent = OrderIntent("SOLUSDT", Direction.LONG, 100.0, 3, 140.0, "sig-no-gate")
    try:
        asyncio.run(adapter.place_entry(intent))
    except ExecutionBlockedError:
        pass
    else:
        raise AssertionError("missing execution gate must fail closed")
    assert not hasattr(rest, "order")
