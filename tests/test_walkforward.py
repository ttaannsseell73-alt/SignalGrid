from signalgrid.backtest.data import HistoricalBar
from signalgrid.backtest.engine import BacktestConfig, parameter_stability
from signalgrid.backtest.walkforward import WalkForwardConfig, run_walk_forward
from signalgrid.signals.engine import SignalConfig


def trend_rows(n=240):
    out = []
    price = 100.0
    for i in range(n):
        o = price
        c = price + 0.2 + (0.05 if i % 3 == 0 else 0.0)
        h = c + 0.08
        l = o - 0.05
        qv = 1000.0
        out.append(HistoricalBar(
            "SOLUSDT", i*60_000, i*60_000+59_999,
            o, h, l, c, 100, qv, 800,
            best_bid=c-0.01, best_ask=c+0.01, bid_depth=20, ask_depth=5,
        ))
        price = c
    return out


def good_cfg(threshold=0.8):
    return SignalConfig(
        structure_lookback=5,
        max_spread_bps=8,
        min_expansion=0.90,
        strong_expansion=1.10,
        min_flow_abs=0.08,
        min_book_abs=0.05,
        entry_threshold=threshold,
    )


def test_walk_forward_selects_candidate_on_train_and_only_reports_oos_trades():
    rows = trend_rows()
    candidates = [good_cfg(0.80), good_cfg(0.99)]
    result = run_walk_forward(
        rows,
        candidates,
        backtest_config=BacktestConfig(taker_fee_bps=0, assumed_spread_bps=0, slippage_bps=0, max_holding_bars=3),
        walk_config=WalkForwardConfig(train_bars=100, test_bars=50, step_bars=50, warmup_bars=60, min_train_trades=2),
    )
    assert len(result.folds) >= 2
    assert any(f.selected_candidate == 0 for f in result.folds)
    assert result.oos_metrics.trades > 0
    for trade in result.oos_trades:
        assert any(f.test_start_ms <= trade.entry_time_ms <= f.test_end_ms for f in result.folds if f.selected_candidate is not None)


def test_parameter_stability_reports_neighborhood_not_single_optimum():
    rows = trend_rows(180)
    report = parameter_stability(
        rows,
        [good_cfg(0.72), good_cfg(0.80), good_cfg(0.88)],
        backtest_config=BacktestConfig(taker_fee_bps=0, assumed_spread_bps=0, slippage_bps=0, max_holding_bars=3),
    )
    assert report.candidates == 3
    assert 0 <= report.positive_fraction <= 1
    assert report.best_expectancy >= report.worst_expectancy
