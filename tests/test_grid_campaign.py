import asyncio
from decimal import Decimal

from signalgrid.execution.binance import BinanceOrderNotFoundError, ExecutionReceipt, client_order_id
from signalgrid.execution.campaign import GridCampaignError, GridCampaignExecutor, GridCampaignRegistry
from signalgrid.execution.grid import GridEntryLevel, GridPlan
from signalgrid.models import Direction
from signalgrid.state.store import StateStore, StoredAlgoOrder, StoredOrder


def plan():
    return GridPlan(
        campaign_id="camp-001",
        symbol="SOLUSDT",
        direction=Direction.LONG,
        reference_price=100.0,
        total_notional_usdt=500.0,
        leverage=3,
        spacing_bps=30.0,
        invalidation=98.0,
        take_profit=100.45,
        entries=(
            GridEntryLevel(0, "MARKET", 100.0, 200.0),
            GridEntryLevel(1, "LIMIT", 99.7, 100.0),
            GridEntryLevel(2, "LIMIT", 99.4, 100.0),
            GridEntryLevel(3, "LIMIT", 99.1, 100.0),
        ),
    )


class FakeAdapter:
    def __init__(self, fail_on=None, missing_algo_once=None):
        self.fail_on = fail_on
        self.missing_algo_once = missing_algo_once
        self.calls = []
        self.canceled_orders = []
        self.canceled_algos = []
        self.emergency = []

    def validate_protective_exit(self, intent):
        self.calls.append(("validate", intent.order_type, intent.idempotency_key))
        if self.fail_on == f"PRECHECK_{intent.order_type}":
            raise RuntimeError(f"invalid {intent.order_type} trigger")

    async def place_entry_receipt(self, intent):
        self.calls.append(("starter", intent.idempotency_key))
        if self.fail_on == "STARTER":
            raise RuntimeError("starter rejected")
        return ExecutionReceipt(intent.symbol, client_order_id("e", intent.idempotency_key), "1001", Decimal("2.0"), Decimal("100"))

    async def place_protective_exit(self, intent):
        self.calls.append((intent.order_type, intent.idempotency_key))
        if self.fail_on == intent.order_type:
            raise RuntimeError(f"fail {intent.order_type}")
        return client_order_id("x", intent.idempotency_key)

    async def place_limit_entry_receipt(self, intent):
        self.calls.append(("limit", intent.idempotency_key))
        if self.fail_on == "LIMIT":
            raise RuntimeError("fail limit")
        idx = intent.idempotency_key.rsplit(":", 1)[-1]
        return ExecutionReceipt(intent.symbol, client_order_id("g", intent.idempotency_key), f"20{idx}", Decimal("1"), Decimal(str(intent.price)))

    async def place_reduce_only_market(self, intent):
        self.emergency.append(intent)
        return client_order_id("r", intent.idempotency_key)

    async def cancel_order(self, symbol, order_id):
        self.canceled_orders.append((symbol, order_id))

    async def cancel_algo_order(self, client_algo_id):
        self.canceled_algos.append(client_algo_id)
        if self.missing_algo_once == client_algo_id:
            self.missing_algo_once = None
            raise BinanceOrderNotFoundError("(-2011, 'Unknown order sent.')")


def test_campaign_open_sequence_and_registry_ownership(tmp_path):
    store = StateStore(tmp_path / "state.db")
    adapter = FakeAdapter()
    executor = GridCampaignExecutor(adapter, store)
    result = asyncio.run(executor.open_campaign(plan()))
    assert result.campaign.status == "ACTIVE"
    assert [x[0] for x in adapter.calls] == ["validate", "validate", "starter", "STOP_MARKET", "TAKE_PROFIT_MARKET", "limit", "limit", "limit"]
    persisted = GridCampaignRegistry(store).active("SOLUSDT")
    assert persisted is not None
    assert persisted.starter_client_id == client_order_id("e", "camp-001:starter")
    assert len(persisted.limit_client_ids) == 3
    assert persisted.stop_client_id == client_order_id("x", "camp-001:stop")
    assert persisted.take_profit_client_id == client_order_id("x", "camp-001:tp")


def test_stop_failure_emergency_closes_starter_and_halts(tmp_path):
    store = StateStore(tmp_path / "state.db")
    adapter = FakeAdapter(fail_on="STOP_MARKET")
    executor = GridCampaignExecutor(adapter, store)
    try:
        asyncio.run(executor.open_campaign(plan()))
    except GridCampaignError:
        pass
    else:
        raise AssertionError("stop failure must fail campaign")
    assert len(adapter.emergency) == 1
    assert store.halted() is True
    assert store.halt_reason() == "GRID_STOP_PLACEMENT_FAILED:SOLUSDT"
    assert GridCampaignRegistry(store).get("SOLUSDT").status == "FAILED"


def test_flat_cleanup_cancels_remaining_limits_and_sibling_algos(tmp_path):
    store = StateStore(tmp_path / "state.db")
    adapter = FakeAdapter()
    executor = GridCampaignExecutor(adapter, store)
    result = asyncio.run(executor.open_campaign(plan()))
    record = result.campaign

    for i, cid in enumerate(record.limit_client_ids, start=1):
        store.upsert_order(StoredOrder(cid, f"30{i}", "SOLUSDT", "BUY", "NEW", "LIMIT", Decimal("1"), Decimal("0"), Decimal("0"), False, 1000+i, None))
    for i, cid in enumerate((record.stop_client_id, record.take_profit_client_id), start=1):
        store.upsert_algo_order(StoredAlgoOrder(cid, f"40{i}", "SOLUSDT", "SELL", "NEW", "CONDITIONAL", "STOP_MARKET" if i == 1 else "TAKE_PROFIT_MARKET", Decimal("98") if i == 1 else Decimal("101"), Decimal("0"), True, False, "", 2000+i))

    assert asyncio.run(executor.cleanup_if_flat("SOLUSDT")) is True
    assert len(adapter.canceled_orders) == 3
    assert set(adapter.canceled_algos) == {record.stop_client_id, record.take_profit_client_id}
    assert GridCampaignRegistry(store).get("SOLUSDT").status == "CLOSED"


def test_recovery_halts_if_live_position_has_no_active_stop(tmp_path):
    store = StateStore(tmp_path / "state.db")
    adapter = FakeAdapter()
    executor = GridCampaignExecutor(adapter, store)
    asyncio.run(executor.open_campaign(plan()))
    store.upsert_account_position("SOLUSDT", "LONG", Decimal("2"), Decimal("100"), Decimal("200"), 3000)
    try:
        asyncio.run(executor.recover_after_reconciliation())
    except GridCampaignError as exc:
        assert "lacks active stop" in str(exc)
    else:
        raise AssertionError("missing stop on recovery must fail closed")
    assert store.halted() is True
    assert store.halt_reason() == "GRID_RECOVERY_MISSING_STOP:SOLUSDT"


def test_flat_cleanup_treats_unknown_algo_order_as_pending_convergence_not_halt(tmp_path):
    store = StateStore(tmp_path / "state.db")
    adapter = FakeAdapter()
    executor = GridCampaignExecutor(adapter, store)
    result = asyncio.run(executor.open_campaign(plan()))
    record = result.campaign

    for i, cid in enumerate((record.stop_client_id, record.take_profit_client_id), start=1):
        store.upsert_algo_order(StoredAlgoOrder(
            cid,
            f"40{i}",
            "SOLUSDT",
            "SELL",
            "NEW",
            "CONDITIONAL",
            "STOP_MARKET" if i == 1 else "TAKE_PROFIT_MARKET",
            Decimal("98") if i == 1 else Decimal("101"),
            Decimal("0"),
            True,
            False,
            "",
            2000 + i,
        ))

    adapter.missing_algo_once = record.stop_client_id
    assert asyncio.run(executor.cleanup_if_flat("SOLUSDT")) is False
    assert store.halted() is False
    assert GridCampaignRegistry(store).get("SOLUSDT").status == "ACTIVE"

    assert asyncio.run(executor.cleanup_if_flat("SOLUSDT")) is True
    assert store.halted() is False
    assert GridCampaignRegistry(store).get("SOLUSDT").status == "CLOSED"


def test_starter_failure_before_exposure_does_not_halt_account(tmp_path):
    store = StateStore(tmp_path / "state.db")
    adapter = FakeAdapter(fail_on="STARTER")
    executor = GridCampaignExecutor(adapter, store)

    try:
        asyncio.run(executor.open_campaign(plan()))
    except GridCampaignError as exc:
        assert "before exposure" in str(exc)
    else:
        raise AssertionError("starter failure must fail the campaign")

    assert store.halted() is False
    assert GridCampaignRegistry(store).get("SOLUSDT").status == "FAILED"
    assert adapter.emergency == []


def test_invalid_stop_precheck_rejects_campaign_before_exposure_without_halt(tmp_path):
    store = StateStore(tmp_path / "state.db")
    adapter = FakeAdapter(fail_on="PRECHECK_STOP_MARKET")
    executor = GridCampaignExecutor(adapter, store)

    try:
        asyncio.run(executor.open_campaign(plan()))
    except GridCampaignError as exc:
        assert "before exposure" in str(exc)
    else:
        raise AssertionError("invalid stop precheck must fail campaign")

    assert store.halted() is False
    assert GridCampaignRegistry(store).get("SOLUSDT").status == "FAILED"
    assert not any(call[0] == "starter" for call in adapter.calls)
    assert adapter.emergency == []
