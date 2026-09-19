from signalgrid.market.binance_events import EventResult
from signalgrid.market.state import Bar
from signalgrid.models import Direction
from signalgrid.ops.run_scalping_paper import build_scalping_paper_runtime
from signalgrid.runtime import RuntimeMode
from signalgrid.sonar.impulse_radar import ImpulseRadar


def test_canonical_scalping_paper_uses_locked_chain(tmp_path):
    runtime = build_scalping_paper_runtime(("BTCUSDT",), str(tmp_path / "paper.db"))
    try:
        assert runtime.config.mode is RuntimeMode.PAPER
        assert isinstance(runtime.scanner.impulse_radar, ImpulseRadar)
        assert runtime.store.get_runtime("coin_sonar_v2") == "ENABLED"
        assert runtime.store.get_runtime("execution_mode") == "PAPER_ONLY"
        assert runtime.paper_broker is not None
        assert runtime.campaign_executor is None
    finally:
        runtime.store.close()


def test_canonical_sonar_signal_risk_paper_chain_opens_bounded_campaign(tmp_path):
    runtime = build_scalping_paper_runtime(("BTCUSDT",), str(tmp_path / "chain.db"))
    state = runtime.router.state("BTCUSDT")
    clock = [1_700_000_000_000]
    runtime.scanner.now_ms = lambda: clock[0]

    # Warm the canonical Signal Hub with closed bars. The final bar is a
    # deterministic liquidity-sweep rejection; no future bar is inspected.
    for i in range(42):
        close = 100.0 + (i % 3 - 1) * 0.03
        state.add_bar(Bar(close - 0.05, close + 0.35, close - 0.35, close, 1000))
    for i in range(18):
        close = 100.0 + (i % 2) * 0.02
        state.add_bar(Bar(close - 0.03, close + 0.12, close - 0.12, close, 900))
    prior_low = min(bar.low for bar in list(state.bars)[-12:])
    state.add_bar(Bar(100.0, 100.20, prior_low - 0.05, 100.08, 1800))
    state.bid_depth, state.ask_depth = 70.0, 30.0

    try:
        # Build the real 60-second adaptive Sonar baseline under the locked
        # SCALPING_SONAR_CONFIG. These events must remain asleep.
        for second in range(60):
            clock[0] = 1_700_000_000_000 + second * 1_000
            state.last_event_time_ms = clock[0]
            state.best_bid, state.best_ask = 99.995, 100.005
            total = float((second + 1) * 1_000)
            state.taker_buy_quote = total * 0.60
            state.taker_sell_quote = total * 0.40
            runtime._process_market_event(EventResult("BTCUSDT", "bookTicker", True))

        assert runtime.stats.sonar_wakes == 0
        assert runtime.stats.signal_hub_evaluations == 0
        assert runtime.stats.campaigns_opened == 0

        # A 51 bps price impulse plus >1.5x adaptive turnover wakes the actual
        # Coin Sonar. The existing Signal Hub, RiskEngine and PAPER broker must
        # then produce one bounded directional campaign.
        clock[0] = 1_700_000_060_000
        state.last_event_time_ms = clock[0]
        state.best_bid, state.best_ask = 100.505, 100.515
        state.taker_buy_quote = 128_000.0
        state.taker_sell_quote = 32_000.0
        runtime._process_market_event(EventResult("BTCUSDT", "bookTicker", True))

        assert runtime.stats.sonar_wakes == 1
        assert runtime.stats.signal_hub_evaluations == 1
        assert runtime.stats.signals_emitted == 1
        assert runtime.stats.open_failures == 0
        assert runtime.stats.campaigns_opened == 1

        assert runtime.paper_broker is not None
        campaign = runtime.paper_broker.active_campaign("BTCUSDT")
        assert campaign is not None
        assert campaign.plan.direction is Direction.LONG
        assert campaign.plan.leverage == 3
        assert campaign.plan.expires_at_ms > state.last_event_time_ms
        assert len(campaign.plan.entries) == 1
        assert campaign.plan.entries[0].kind == "MARKET"
        assert campaign.fills
    finally:
        runtime.store.close()
