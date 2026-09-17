from collections import deque
from time import time

from signalgrid.execution.grid import GridConfig, GridPlanError, build_grid_plan
from signalgrid.execution.paper import PaperExecutionError, PaperGridBroker
from signalgrid.market.state import Bar, SymbolState
from signalgrid.models import Direction, RiskDecision, Signal


def state(symbol="SOLUSDT"):
    bars = deque(maxlen=256)
    price = 100.0
    for _ in range(60):
        o = price
        c = price + 0.08
        bars.append(Bar(o, c + 0.30, o - 0.20, c, 100))
        price = c
    s = SymbolState(symbol=symbol, bars=bars)
    s.best_bid = price - 0.01
    s.best_ask = price + 0.01
    s.bid_depth = 20
    s.ask_depth = 10
    s.last_event_time_ms = 1_700_000_000_000
    return s


def signal(s, direction=Direction.LONG):
    ref = (s.best_bid + s.best_ask) / 2
    invalidation = ref * (0.98 if direction is Direction.LONG else 1.02)
    now = time()
    return Signal(s.symbol, direction, 0.85, "EXPANSION", "BREAKOUT_ACCEPTANCE", invalidation, True, now + 20, now)


def decision():
    return RiskDecision(True, "APPROVED", 500.0, 3)


def test_bounded_long_grid_has_starter_three_pullbacks_and_global_tp():
    s = state()
    plan = build_grid_plan(signal(s), decision(), s, GridConfig(entry_levels=4, starter_fraction=0.4))
    assert len(plan.entries) == 4
    assert plan.entries[0].kind == "MARKET"
    assert round(sum(x.notional_usdt for x in plan.entries), 8) == 500.0
    assert plan.entries[0].notional_usdt == 200.0
    assert plan.entries[1].price < plan.reference_price
    assert plan.entries[2].price < plan.entries[1].price
    assert plan.entries[3].price < plan.entries[2].price
    assert plan.take_profit > plan.reference_price
    assert plan.invalidation < plan.reference_price
    assert 15 <= plan.spacing_bps <= 120


def test_short_grid_is_mirrored_and_campaign_id_is_event_stable():
    s = state()
    sig = signal(s, Direction.SHORT)
    first = build_grid_plan(sig, decision(), s)
    second = build_grid_plan(sig, decision(), s)
    assert first.campaign_id == second.campaign_id
    assert first.entries[1].price > first.reference_price
    assert first.take_profit < first.reference_price
    assert first.invalidation > first.reference_price


def test_grid_fails_without_volatility_warmup():
    s = state()
    s.bars = deque(list(s.bars)[-5:], maxlen=256)
    try:
        build_grid_plan(signal(s), decision(), s)
    except GridPlanError as exc:
        assert "NATR" in str(exc)
    else:
        raise AssertionError("warmup failure must reject plan")


def test_paper_campaign_fills_pullback_and_closes_at_global_tp():
    s = state()
    plan = build_grid_plan(signal(s), decision(), s)
    broker = PaperGridBroker(suppress_same_event_reopen=False)
    campaign = broker.open_campaign(plan)
    assert len(campaign.fills) == 1
    assert len(broker.position_views()) == 1

    first_limit = plan.entries[1]
    s.best_bid = first_limit.price - 0.02
    s.best_ask = first_limit.price - 0.01
    campaign = broker.on_state(s)
    assert campaign is not None
    assert first_limit.index in campaign.filled_indices
    assert campaign.notional_usdt > plan.entries[0].notional_usdt

    s.best_bid = plan.take_profit + 0.01
    s.best_ask = plan.take_profit + 0.02
    closed = broker.on_state(s)
    assert closed is not None
    assert closed.status == "CLOSED"
    assert closed.close_reason == "TAKE_PROFIT"
    assert broker.active_campaign(plan.symbol) is None
    assert broker.position_views() == []


def test_same_market_event_cannot_close_and_reopen_paper_campaign():
    s = state()
    plan = build_grid_plan(signal(s), decision(), s)
    broker = PaperGridBroker()
    broker.open_campaign(plan)

    s.best_bid = plan.take_profit + 0.01
    s.best_ask = plan.take_profit + 0.02
    closed = broker.on_state(s)
    assert closed is not None and closed.status == "CLOSED"
    assert broker.position_views() == []
    assert broker.active_campaign(plan.symbol) is closed

    try:
        broker.open_campaign(plan)
    except PaperExecutionError:
        pass
    else:
        raise AssertionError("same market event must not close and immediately reopen")

    s.last_event_time_ms += 1
    assert broker.on_state(s) is None
    assert broker.active_campaign(plan.symbol) is None
    next_plan = build_grid_plan(signal(s), decision(), s)
    broker.open_campaign(next_plan)
    assert broker.active_campaign(plan.symbol) is not None


def test_only_one_campaign_per_symbol_and_no_unbounded_refill():
    s = state()
    plan = build_grid_plan(signal(s), decision(), s)
    broker = PaperGridBroker()
    campaign = broker.open_campaign(plan)
    try:
        broker.open_campaign(plan)
    except PaperExecutionError:
        pass
    else:
        raise AssertionError("duplicate symbol campaign must be rejected")

    deepest = plan.entries[-1]
    s.best_bid = deepest.price - 0.02
    s.best_ask = deepest.price - 0.01
    broker.on_state(s)
    before = len(campaign.fills)
    broker.on_state(s)
    broker.on_state(s)
    assert len(campaign.fills) == before == len(plan.entries)
