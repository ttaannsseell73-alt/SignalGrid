import json
from dataclasses import asdict
from decimal import Decimal

from signalgrid.execution.campaign import GridCampaignRecord
from signalgrid.ops.health import collect_health
from signalgrid.state.store import StateStore, StoredAccountPosition, StoredAlgoOrder, StoredOrder


def _record():
    return GridCampaignRecord(
        campaign_id="cmp-1",
        symbol="SOLUSDT",
        direction="LONG",
        status="ACTIVE",
        reference_price=100.0,
        total_notional_usdt=100.0,
        leverage=3,
        spacing_bps=25.0,
        invalidation=95.0,
        take_profit=102.0,
        starter_client_id="sg-e-starter",
        limit_client_ids=("sg-g-1", "sg-g-2", "sg-g-3"),
        stop_client_id="sg-x-stop",
        take_profit_client_id="sg-x-tp",
        created_at_ms=1,
        updated_at_ms=1,
    )


def _configure_live(store):
    store.set_runtime("runtime_mode", "TESTNET")
    store.set_runtime("runtime_status", "TESTNET_LIVE")
    store.set_runtime("user_stream_status", "LIVE")
    store.set_execution_ready(True)
    store.set_runtime("runtime_stats", json.dumps({"campaigns_opened": 1, "open_failures": 0}))


def _install_campaign(store, record=None):
    record = record or _record()
    store.set_runtime("grid_campaigns_v1", json.dumps({record.symbol: asdict(record)}))
    return record


def _install_position(store):
    store.upsert_account_position("SOLUSDT", "LONG", Decimal("1"), Decimal("100"), Decimal("100"), 10)


def _algo(client_id, algo_id):
    return StoredAlgoOrder(
        client_algo_id=client_id,
        algo_id=algo_id,
        symbol="SOLUSDT",
        side="SELL",
        status="NEW",
        algo_type="CONDITIONAL",
        order_type="STOP_MARKET" if client_id.endswith("stop") else "TAKE_PROFIT_MARKET",
        trigger_price=Decimal("95") if client_id.endswith("stop") else Decimal("102"),
        quantity=Decimal("0"),
        close_position=True,
        reduce_only=False,
        actual_order_id="",
        event_time=10,
    )


def test_healthy_testnet_state_requires_owned_stop_and_take_profit(tmp_path):
    store = StateStore(tmp_path / "state.db")
    _configure_live(store)
    record = _install_campaign(store)
    _install_position(store)
    store.upsert_algo_order(_algo(record.stop_client_id, "algo-stop"))
    store.upsert_algo_order(_algo(record.take_profit_client_id, "algo-tp"))

    health = collect_health(store)
    assert health.healthy
    assert health.account_positions == 1
    assert health.active_campaigns == 1
    assert health.missing_stops == ()
    assert health.missing_take_profits == ()


def test_missing_stop_fails_health_even_when_execution_gate_is_open(tmp_path):
    store = StateStore(tmp_path / "state.db")
    _configure_live(store)
    record = _install_campaign(store)
    _install_position(store)
    store.upsert_algo_order(_algo(record.take_profit_client_id, "algo-tp"))

    health = collect_health(store)
    assert not health.healthy
    assert health.missing_stops == ("SOLUSDT",)


def test_position_without_campaign_is_orphan_and_unhealthy(tmp_path):
    store = StateStore(tmp_path / "state.db")
    _configure_live(store)
    _install_position(store)

    health = collect_health(store)
    assert not health.healthy
    assert health.orphan_positions == ("SOLUSDT",)


def test_unowned_active_order_and_algo_are_detected(tmp_path):
    store = StateStore(tmp_path / "state.db")
    _configure_live(store)
    record = _install_campaign(store)
    _install_position(store)
    store.upsert_algo_order(_algo(record.stop_client_id, "algo-stop"))
    store.upsert_algo_order(_algo(record.take_profit_client_id, "algo-tp"))
    store.upsert_order(
        StoredOrder(
            client_order_id="sg-g-orphan",
            exchange_order_id="123",
            symbol="SOLUSDT",
            side="BUY",
            status="NEW",
            order_type="LIMIT",
            orig_qty=Decimal("1"),
            filled_qty=Decimal("0"),
            avg_price=Decimal("0"),
            reduce_only=False,
            event_time=10,
            last_trade_id=None,
        )
    )
    store.upsert_algo_order(_algo("sg-x-orphan", "algo-orphan"))

    health = collect_health(store)
    assert not health.healthy
    assert health.orphan_orders == ("sg-g-orphan",)
    assert health.orphan_algo_orders == ("sg-x-orphan",)
