from signalgrid.backtest.data import FundingPoint, HistoricalBar
from signalgrid.backtest.engine import BacktestConfig, BacktestDataQualityError, run_backtest
from signalgrid.models import Direction, Signal


class OneShotEngine:
    def __init__(self, direction=Direction.LONG, invalidation=95.0, emit_on=1):
        self.calls = 0
        self.direction = direction
        self.invalidation = invalidation
        self.emit_on = emit_on

    def evaluate(self, state):
        self.calls += 1
        if self.calls == self.emit_on:
            return Signal(state.symbol, self.direction, 0.9, "EXPANSION", "TEST", self.invalidation, True, 9999999999, 0)
        return Signal.pass_signal(state.symbol)


def row(i, o, h, l, c):
    return HistoricalBar(
        "BTCUSDT", i * 60_000, i * 60_000 + 59_999,
        o, h, l, c, 10, 1000, 700,
        best_bid=c - 0.01, best_ask=c + 0.01, bid_depth=12, ask_depth=8,
    )


def test_signal_at_bar_close_enters_only_next_bar_open():
    rows = [row(0, 100, 101, 99, 100), row(1, 110, 112, 109, 111), row(2, 111, 113, 110, 112)]
    cfg = BacktestConfig(taker_fee_bps=0, assumed_spread_bps=0, slippage_bps=0, max_holding_bars=1)
    result = run_backtest(rows, backtest_config=cfg, engine=OneShotEngine())
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.signal_time_ms == rows[0].close_time_ms
    assert trade.entry_time_ms == rows[1].open_time_ms
    assert trade.entry_price == 110


def test_gap_through_invalidation_skips_entry():
    rows = [row(0, 100, 101, 99, 100), row(1, 94, 96, 93, 95), row(2, 95, 96, 94, 95)]
    result = run_backtest(rows, backtest_config=BacktestConfig(require_book=True), engine=OneShotEngine(invalidation=95.0))
    assert result.metrics.trades == 0
    assert result.skipped_invalidated_before_entry == 1


def test_fees_spread_slippage_and_funding_reduce_long_pnl():
    rows = [row(0, 100, 101, 99, 100), row(1, 100, 102, 99, 101), row(2, 101, 104, 100, 103)]
    funding = [FundingPoint("BTCUSDT", rows[1].open_time_ms + 1, 0.001)]
    clean = run_backtest(rows, backtest_config=BacktestConfig(taker_fee_bps=0, assumed_spread_bps=0, slippage_bps=0, max_holding_bars=2), engine=OneShotEngine(), funding_points=())
    costed = run_backtest(rows, backtest_config=BacktestConfig(taker_fee_bps=5, assumed_spread_bps=2, slippage_bps=1, max_holding_bars=2), engine=OneShotEngine(), funding_points=funding)
    assert costed.metrics.net_pnl < clean.metrics.net_pnl
    assert costed.metrics.fees > 0
    assert costed.metrics.funding_cost > 0


def test_missing_book_fails_instead_of_synthesizing():
    r = HistoricalBar("BTCUSDT", 0, 59_999, 100, 101, 99, 100, 10, 1000, 600)
    try:
        run_backtest([r], backtest_config=BacktestConfig(require_book=True), engine=OneShotEngine())
    except BacktestDataQualityError as exc:
        assert "historical book" in str(exc)
    else:
        raise AssertionError("missing book data must fail closed")


def test_take_profit_uses_signal_time_natr_profile_and_exits_before_max_hold():
    rows = []
    for i in range(15):
        rows.append(row(i, 100.0, 100.2, 99.8, 100.0))
    rows.append(row(15, 100.0, 100.25, 99.95, 100.15))
    rows.append(row(16, 100.15, 100.2, 100.0, 100.1))

    cfg = BacktestConfig(
        taker_fee_bps=0,
        assumed_spread_bps=0,
        slippage_bps=0,
        max_holding_bars=3,
        take_profit_enabled=True,
        tp_spacing_natr_multiplier=0.20,
        tp_min_spacing_bps=10.0,
        tp_max_spacing_bps=10.0,
        tp_steps=1.0,
    )
    result = run_backtest(
        rows,
        backtest_config=cfg,
        engine=OneShotEngine(invalidation=99.0, emit_on=15),
    )
    assert result.metrics.trades == 1
    trade = result.trades[0]
    assert trade.exit_reason == "TAKE_PROFIT"
    assert round(trade.exit_price, 6) == 100.1


def test_same_bar_stop_and_target_uses_conservative_stop_first():
    rows = []
    for i in range(15):
        rows.append(row(i, 100.0, 100.2, 99.8, 100.0))
    rows.append(row(15, 100.0, 100.25, 98.5, 100.1))
    rows.append(row(16, 100.1, 100.2, 100.0, 100.1))

    cfg = BacktestConfig(
        taker_fee_bps=0,
        assumed_spread_bps=0,
        slippage_bps=0,
        max_holding_bars=3,
        take_profit_enabled=True,
        tp_min_spacing_bps=10.0,
        tp_max_spacing_bps=10.0,
        tp_steps=1.0,
    )
    result = run_backtest(
        rows,
        backtest_config=cfg,
        engine=OneShotEngine(invalidation=99.0, emit_on=15),
    )
    assert result.metrics.trades == 1
    assert result.trades[0].exit_reason == "INVALIDATION_STOP"


def test_reward_risk_take_profit_uses_actual_entry_stop_distance():
    rows = [row(0, 100, 101, 99, 100)]
    for i in range(1, 16):
        rows.append(row(i, 100.0, 100.2, 99.8, 100.0))
    rows.append(row(16, 100.0, 103.0, 99.5, 102.0))
    cfg = BacktestConfig(
        taker_fee_bps=0,
        assumed_spread_bps=0,
        slippage_bps=0,
        max_holding_bars=2,
        take_profit_enabled=True,
        min_take_profit_bps=0.0,
        tp_reward_risk_multiple=2.0,
    )
    result = run_backtest(
        rows,
        backtest_config=cfg,
        engine=OneShotEngine(invalidation=99.0, emit_on=15),
    )
    assert result.metrics.trades == 1
    trade = result.trades[0]
    assert trade.exit_reason == "TAKE_PROFIT"
    # Entry 100, stop 99 => 100 bps risk. 2R target => 102.
    assert round(trade.exit_price, 6) == 102.0
    assert round(trade.initial_stop_bps, 6) == 100.0
