from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from signalgrid.backtest.data import FundingPoint, HistoricalBar
from signalgrid.backtest.engine import BacktestConfig, BacktestMetrics, SimulatedTrade, metrics_from_trades, run_backtest
from signalgrid.signals.engine import SignalConfig


@dataclass(frozen=True, slots=True)
class WalkForwardConfig:
    train_bars: int = 2_000
    test_bars: int = 500
    step_bars: int = 500
    warmup_bars: int = 60
    min_train_trades: int = 3


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    fold: int
    train_start_ms: int
    train_end_ms: int
    test_start_ms: int
    test_end_ms: int
    selected_candidate: int | None
    train_metrics: BacktestMetrics | None
    test_metrics: BacktestMetrics | None


@dataclass(frozen=True, slots=True)
class WalkForwardResult:
    symbol: str
    folds: tuple[WalkForwardFold, ...]
    oos_trades: tuple[SimulatedTrade, ...]
    oos_metrics: BacktestMetrics


def run_walk_forward(
    rows: Sequence[HistoricalBar],
    candidates: Sequence[SignalConfig],
    *,
    backtest_config: BacktestConfig | None = None,
    walk_config: WalkForwardConfig | None = None,
    funding_points: Sequence[FundingPoint] = (),
) -> WalkForwardResult:
    if not candidates:
        raise ValueError("at least one SignalConfig candidate is required")
    if not rows:
        cfg = backtest_config or BacktestConfig()
        return WalkForwardResult("", (), (), metrics_from_trades((), cfg.starting_equity))
    cfg = backtest_config or BacktestConfig()
    wf = walk_config or WalkForwardConfig()
    ordered = sorted(rows, key=lambda r: r.open_time_ms)
    if wf.train_bars <= 0 or wf.test_bars <= 0 or wf.step_bars <= 0:
        raise ValueError("walk-forward window sizes must be positive")
    folds: list[WalkForwardFold] = []
    oos: list[SimulatedTrade] = []
    fold_no = 0
    train_start = 0
    while train_start + wf.train_bars + wf.test_bars <= len(ordered):
        train_end = train_start + wf.train_bars
        test_end = train_end + wf.test_bars
        train = ordered[train_start:train_end]
        test = ordered[train_end:test_end]
        ranked: list[tuple[float, float, int, BacktestMetrics]] = []
        for i, candidate in enumerate(candidates):
            result = run_backtest(train, signal_config=candidate, backtest_config=cfg, funding_points=funding_points)
            if result.metrics.trades >= wf.min_train_trades:
                ranked.append((result.metrics.expectancy, result.metrics.net_pnl, i, result.metrics))
        if not ranked:
            folds.append(WalkForwardFold(
                fold_no, train[0].open_time_ms, train[-1].close_time_ms,
                test[0].open_time_ms, test[-1].close_time_ms, None, None, None,
            ))
        else:
            ranked.sort(reverse=True)
            _, _, selected, train_metrics = ranked[0]
            context_start = max(train_start, train_end - wf.warmup_bars)
            context = ordered[context_start:test_end]
            test_result = run_backtest(
                context,
                signal_config=candidates[selected],
                backtest_config=cfg,
                funding_points=funding_points,
                trade_start_ms=test[0].open_time_ms,
                trade_end_ms=test[-1].close_time_ms + 1,
            )
            oos.extend(test_result.trades)
            folds.append(WalkForwardFold(
                fold_no, train[0].open_time_ms, train[-1].close_time_ms,
                test[0].open_time_ms, test[-1].close_time_ms,
                selected, train_metrics, test_result.metrics,
            ))
        fold_no += 1
        train_start += wf.step_bars
    return WalkForwardResult(ordered[0].symbol.upper(), tuple(folds), tuple(oos), metrics_from_trades(oos, cfg.starting_equity))
