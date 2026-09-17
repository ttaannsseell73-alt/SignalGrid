from signalgrid.backtest.data import FundingPoint, HistoricalBar
from signalgrid.backtest.engine import BacktestConfig, BacktestDataQualityError, run_backtest
from signalgrid.models import Direction, Signal


class OneShotEngine:
    def __init__(self, direction=Direction.LONG, invalidation=95.0):
        self.calls = 0
        self.direction = direction
        self.invalidation = invalidation

    def evaluate(self, state):
        self.calls += 1
        if self.calls == 1:
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
