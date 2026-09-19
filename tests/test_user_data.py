from decimal import Decimal

from signalgrid.state.store import StateStore
from signalgrid.state.user_data import BinanceUserDataProcessor


def order_event(status="NEW", filled="0", event_time=1000, trade_id=-1, client_id="sg-e-abc"):
    return {
        "e": "ORDER_TRADE_UPDATE",
        "E": event_time,
        "T": event_time,
        "o": {
            "s": "SOLUSDT",
            "c": client_id,
            "S": "BUY",
            "o": "MARKET",
            "q": "1.0",
            "ap": "143.2" if filled != "0" else "0",
            "x": "TRADE" if filled != "0" else "NEW",
            "X": status,
            "i": 123,
            "z": filled,
            "t": trade_id,
            "R": False,
            "ps": "BOTH",
            "T": event_time,
        },
    }


def algo_event(status="NEW", event_time=1000, client_id="sg-x-stop", quantity="0", close_position=True):
    return {
        "e": "ALGO_UPDATE",
        "E": event_time,
        "T": event_time,
        "o": {
            "caid": client_id,
            "aid": 901,
            "at": "CONDITIONAL",
            "o": "STOP_MARKET",
            "s": "SOLUSDT",
            "S": "SELL",
            "ps": "BOTH",
            "q": quantity,
            "X": status,
            "ai": "",
            "tp": "140.03",
            "cp": close_position,
            "R": False,
        },
    }


def test_order_updates_are_idempotent_and_fill_progresses(tmp_path):
    store = StateStore(tmp_path / "state.db")
    p = BinanceUserDataProcessor(store)
    first = p.on_message(order_event())
    assert first.changed
    saved = store.get_order("sg-e-abc")
    assert saved is not None and saved.status == "NEW"

    duplicate = p.on_message(order_event())
    assert duplicate.duplicate and not duplicate.changed

    filled = p.on_message(order_event("FILLED", "1.0", 1001, 777))
    assert filled.changed
    saved = store.get_order("sg-e-abc")
    assert saved is not None
    assert saved.status == "FILLED"
    assert saved.filled_qty == Decimal("1.0")
    assert saved.last_trade_id == 777
    assert store.list_orders(active_only=True) == []


def test_stale_order_event_is_ignored(tmp_path):
    store = StateStore(tmp_path / "state.db")
    p = BinanceUserDataProcessor(store)
    p.on_message(order_event("PARTIALLY_FILLED", "0.5", 2000, 1))
    result = p.on_message(order_event("NEW", "0", 1000, -1))
    assert result.stale
    assert store.get_order("sg-e-abc").filled_qty == Decimal("0.5")


def test_fill_regression_halts(tmp_path):
    store = StateStore(tmp_path / "state.db")
    p = BinanceUserDataProcessor(store)
    p.on_message(order_event("PARTIALLY_FILLED", "0.6", 1000, 1))
    result = p.on_message(order_event("PARTIALLY_FILLED", "0.4", 1001, 2))
    assert "ORDER_FILL_REGRESSION" in result.detail
    assert store.halted()
    assert not store.execution_ready()


def test_foreign_order_event_halts(tmp_path):
    store = StateStore(tmp_path / "state.db")
    p = BinanceUserDataProcessor(store)
    result = p.on_message(order_event(client_id="manual-order"))
    assert "FOREIGN_ORDER_EVENT" in result.detail
    assert store.halted()


def test_algo_order_is_tracked_and_deduped(tmp_path):
    store = StateStore(tmp_path / "state.db")
    p = BinanceUserDataProcessor(store)
    assert p.on_message(algo_event()).changed
    algo = store.get_algo_order("sg-x-stop")
    assert algo is not None
    assert algo.algo_id == "901"
    assert algo.trigger_price == Decimal("140.03")
    assert algo.close_position
    assert p.on_message(algo_event()).duplicate


def test_account_update_opens_and_closes_position(tmp_path):
    store = StateStore(tmp_path / "state.db")
    p = BinanceUserDataProcessor(store)
    opened = {
        "e": "ACCOUNT_UPDATE",
        "E": 3000,
        "T": 3000,
        "a": {"P": [{"s": "SOLUSDT", "pa": "1.5", "ep": "140", "ps": "BOTH"}]},
    }
    assert p.on_message(opened).changed
    pos = store.get_account_position("SOLUSDT")
    assert pos is not None
    assert pos.direction == "LONG"
    assert pos.quantity == Decimal("1.5")
    assert pos.notional_usdt == Decimal("210.0")

    closed = {
        "e": "ACCOUNT_UPDATE",
        "E": 3001,
        "T": 3001,
        "a": {"P": [{"s": "SOLUSDT", "pa": "0", "ep": "0", "ps": "BOTH"}]},
    }
    assert p.on_message(closed).changed
    assert store.get_account_position("SOLUSDT") is None
    assert store.list_positions() == []


def test_hedge_mode_event_halts(tmp_path):
    store = StateStore(tmp_path / "state.db")
    p = BinanceUserDataProcessor(store)
    event = {
        "e": "ACCOUNT_UPDATE",
        "E": 4000,
        "T": 4000,
        "a": {"P": [{"s": "SOLUSDT", "pa": "1", "ep": "140", "ps": "LONG"}]},
    }
    result = p.on_message(event)
    assert "HEDGE_MODE_UNSUPPORTED" in result.detail
    assert store.halted()


def test_listen_key_expiry_closes_execution_without_permanent_halt(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.set_execution_ready(True)
    p = BinanceUserDataProcessor(store)
    result = p.on_message({"e": "listenKeyExpired", "E": 5000})
    assert result.reconnect_required
    assert not store.execution_ready()
    assert not store.halted()


def test_margin_call_is_fail_closed(tmp_path):
    store = StateStore(tmp_path / "state.db")
    p = BinanceUserDataProcessor(store)
    p.on_message({"e": "MARGIN_CALL", "E": 6000})
    assert store.halted()
    assert store.halt_reason() == "MARGIN_CALL"


def test_close_position_algo_quantity_drift_does_not_halt(tmp_path):
    store = StateStore(tmp_path / "state.db")
    p = BinanceUserDataProcessor(store)

    first = p.on_message(algo_event(quantity="279.1", event_time=1000))
    assert first.changed
    second = p.on_message(algo_event(quantity="0", event_time=1001))

    assert second.changed
    assert not store.halted()
    saved = store.get_algo_order("sg-x-stop")
    assert saved is not None
    assert saved.close_position
    assert saved.quantity == Decimal("0")


def test_non_close_position_algo_quantity_change_still_halts(tmp_path):
    store = StateStore(tmp_path / "state.db")
    p = BinanceUserDataProcessor(store)

    p.on_message(algo_event(quantity="1.0", close_position=False, event_time=1000))
    result = p.on_message(algo_event(quantity="2.0", close_position=False, event_time=1001))

    assert "ALGO_IDENTITY_CONFLICT" in result.detail
    assert ":qty:1.0!=2.0" in result.detail
    assert store.halted()


def test_algo_triggered_to_finished_is_valid_lifecycle(tmp_path):
    store = StateStore(tmp_path / "state.db")
    p = BinanceUserDataProcessor(store)

    assert p.on_message(algo_event(status="NEW", event_time=1000)).changed
    triggered = p.on_message(algo_event(status="TRIGGERED", event_time=1001))
    assert triggered.changed
    assert not store.halted()

    finished = p.on_message(algo_event(status="FINISHED", event_time=1002))
    assert finished.changed
    assert not store.halted()
    saved = store.get_algo_order("sg-x-stop")
    assert saved is not None
    assert saved.status == "FINISHED"


def test_algo_triggered_back_to_new_still_halts(tmp_path):
    store = StateStore(tmp_path / "state.db")
    p = BinanceUserDataProcessor(store)

    p.on_message(algo_event(status="TRIGGERED", event_time=1000))
    result = p.on_message(algo_event(status="NEW", event_time=1001))

    assert "ALGO_STATUS_REGRESSION" in result.detail
    assert store.halted()


def test_finished_algo_status_mutation_still_halts(tmp_path):
    store = StateStore(tmp_path / "state.db")
    p = BinanceUserDataProcessor(store)

    p.on_message(algo_event(status="FINISHED", event_time=1000))
    result = p.on_message(algo_event(status="TRIGGERED", event_time=1001))

    assert "TERMINAL_ALGO_MUTATION" in result.detail
    assert store.halted()
