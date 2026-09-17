from decimal import Decimal

import pytest

from signalgrid.state.reconciliation import (
    AccountReconciler,
    AccountSnapshot,
    BinanceRestSnapshotProvider,
    ExchangeAlgoSnapshot,
    ExchangeOrderSnapshot,
    ExchangePositionSnapshot,
    ReconciliationUnavailableError,
    StateMismatchError,
    parse_open_algo_orders,
)
from signalgrid.state.store import StateStore, StoredAlgoOrder, StoredOrder


class SnapshotProvider:
    def __init__(self, snapshot):
        self.value = snapshot

    def snapshot(self):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


def empty_snapshot():
    return AccountSnapshot((), (), ())


def test_empty_reconciliation_activates_execution(tmp_path):
    store = StateStore(tmp_path / "state.db")
    report = AccountReconciler(store, SnapshotProvider(empty_snapshot())).reconcile()
    assert report.ok
    assert report.order_count == report.algo_order_count == report.position_count == 0
    assert store.execution_ready()


def test_matching_order_algo_and_position_reconcile(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.upsert_order(StoredOrder("sg-e-a", "1", "SOLUSDT", "BUY", "NEW", "LIMIT", Decimal("1"), Decimal("0"), Decimal("0"), False, 1, None))
    store.upsert_algo_order(StoredAlgoOrder("sg-x-a", "9", "SOLUSDT", "SELL", "NEW", "CONDITIONAL", "STOP_MARKET", Decimal("140"), Decimal("0"), True, False, "", 1))
    store.upsert_account_position("SOLUSDT", "LONG", "1", "143", "143", 1)
    snap = AccountSnapshot(
        (ExchangeOrderSnapshot("sg-e-a", "1", "SOLUSDT", "BUY", "NEW", "LIMIT", Decimal("1"), Decimal("0"), Decimal("0"), False),),
        (ExchangeAlgoSnapshot("sg-x-a", "9", "SOLUSDT", "SELL", "NEW", "CONDITIONAL", "STOP_MARKET", Decimal("140"), Decimal("0"), True, False, ""),),
        (ExchangePositionSnapshot("SOLUSDT", "LONG", Decimal("1"), Decimal("143")),),
    )
    report = AccountReconciler(store, SnapshotProvider(snap)).reconcile()
    assert report.ok and report.algo_order_count == 1
    assert store.execution_ready()


def test_unknown_remote_position_halts(tmp_path):
    store = StateStore(tmp_path / "state.db")
    snap = AccountSnapshot((), (), (ExchangePositionSnapshot("SOLUSDT", "LONG", Decimal("1"), Decimal("143")),))
    with pytest.raises(StateMismatchError, match="unknown_remote_position"):
        AccountReconciler(store, SnapshotProvider(snap)).reconcile()
    assert store.halted()
    assert not store.execution_ready()


def test_missing_remote_active_order_halts(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.upsert_order(StoredOrder("sg-e-a", "1", "SOLUSDT", "BUY", "NEW", "LIMIT", Decimal("1"), Decimal("0"), Decimal("0"), False, 1, None))
    with pytest.raises(StateMismatchError, match="missing_remote_order"):
        AccountReconciler(store, SnapshotProvider(empty_snapshot())).reconcile()
    assert store.halted()


def test_rest_unavailable_blocks_execution_without_permanent_halt(tmp_path):
    store = StateStore(tmp_path / "state.db")
    with pytest.raises(ReconciliationUnavailableError):
        AccountReconciler(store, SnapshotProvider(ReconciliationUnavailableError("offline"))).reconcile()
    assert not store.execution_ready()
    assert not store.halted()


def test_parse_open_algo_orders_current_binance_shape():
    rows = [{
        "algoId": 9,
        "clientAlgoId": "sg-x-a",
        "algoType": "CONDITIONAL",
        "orderType": "STOP_MARKET",
        "symbol": "SOLUSDT",
        "side": "SELL",
        "quantity": "0",
        "algoStatus": "NEW",
        "actualOrderId": "",
        "triggerPrice": "140.03",
        "closePosition": True,
        "reduceOnly": False,
    }]
    parsed = parse_open_algo_orders(rows)
    assert len(parsed) == 1
    assert parsed[0].client_algo_id == "sg-x-a"
    assert parsed[0].trigger_price == Decimal("140.03")


def test_provider_uses_all_three_authoritative_rest_snapshots():
    class Rest:
        def current_all_open_orders(self):
            return []
        def current_all_algo_open_orders(self):
            return []
        def position_information_v3(self):
            return []
    snapshot = BinanceRestSnapshotProvider(Rest()).snapshot()
    assert snapshot == empty_snapshot()
