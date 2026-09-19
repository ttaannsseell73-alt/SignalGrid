from __future__ import annotations

from dataclasses import dataclass, replace
from statistics import mean
from typing import Sequence

from signalgrid.backtest.data import FundingPoint, HistoricalBar
from signalgrid.backtest.engine import BacktestConfig, BacktestResult, run_backtest
from signalgrid.signals.scalping import ScalpingConfig, ScalpingSignalEngine


@dataclass(frozen=True, slots=True)
class ScalpingValidationGate:
    min_trades: int = 50
    min_base_expectancy_usdt: float = 0.0
    min_stress_expectancy_usdt: float = 0.0
    min_base_profit_factor: float = 1.05
    max_base_drawdown_pct: float = 0.15
    max_base_cost_share: float = 0.70
    min_history_days: float = 28.0
    oos_fraction: float = 0.30
    min_oos_trades: int = 15
    min_oos_expectancy_usdt: float = 0.0
    min_oos_stress_expectancy_usdt: float = 0.0
    min_oos_profit_factor: float = 1.00
    min_robust_positive_fraction: float = 0.60


@dataclass(frozen=True, slots=True)
class SliceMetrics:
    key: str
    trades: int
    win_rate: float
    expectancy: float
    avg_holding_bars: float
    avg_mae_usdt: float
    avg_mfe_usdt: float


@dataclass(frozen=True, slots=True)
class RobustnessMetrics:
    candidates: int
    positive_oos_stress: int
    positive_fraction: float
    median_oos_stress_expectancy: float
    worst_oos_stress_expectancy: float
    best_oos_stress_expectancy: float


@dataclass(frozen=True, slots=True)
class ScalpingValidationReport:
    base: BacktestResult
    stress: BacktestResult
    oos_base: BacktestResult
    oos_stress: BacktestResult
    by_setup: tuple[SliceMetrics, ...]
    by_regime: tuple[SliceMetrics, ...]
    history_span_days: float
    oos_start_ms: int | None
    historical_microstructure_scope: str
    robustness: RobustnessMetrics
    passed: bool
    reasons: tuple[str, ...]


def _slice_metrics(trades, attr: str) -> tuple[SliceMetrics, ...]:
    groups: dict[str, list] = {}
    for trade in trades:
        key = str(getattr(trade, attr) or "UNKNOWN")
        groups.setdefault(key, []).append(trade)

    out: list[SliceMetrics] = []
    for key, items in sorted(groups.items()):
        pnls = [float(t.net_pnl) for t in items]
        wins = sum(1 for x in pnls if x > 0)
        out.append(
            SliceMetrics(
                key=key,
                trades=len(items),
                win_rate=wins / len(items) if items else 0.0,
                expectancy=mean(pnls) if pnls else 0.0,
                avg_holding_bars=mean(float(t.holding_bars) for t in items) if items else 0.0,
                avg_mae_usdt=mean(float(t.mae_usdt) for t in items) if items else 0.0,
                avg_mfe_usdt=mean(float(t.mfe_usdt) for t in items) if items else 0.0,
            )
        )
    return tuple(out)


def scalping_parameter_neighborhood(base: ScalpingConfig) -> tuple[ScalpingConfig, ...]:
    """Small local perturbation set used to reject knife-edge parameter fits."""
    out: list[ScalpingConfig] = []
    for score_delta in (-0.03, 0.0, 0.03):
        for expansion_delta in (-0.05, 0.0, 0.05):
            out.append(
                replace(
                    base,
                    min_score=round(min(0.95, max(0.50, base.min_score + score_delta)), 6),
                    min_expansion=round(max(0.50, base.min_expansion + expansion_delta), 6),
                )
            )
    return tuple(out)


def _robustness_rows(
    rows: Sequence[HistoricalBar],
    trade_start_ms: int | None,
    *,
    warmup_bars: int = 80,
) -> Sequence[HistoricalBar]:
    if trade_start_ms is None or not rows:
        return rows
    ordered = sorted(rows, key=lambda row: row.open_time_ms)
    first_oos = next(
        (i for i, row in enumerate(ordered) if row.open_time_ms >= trade_start_ms),
        len(ordered),
    )
    return ordered[max(0, first_oos - warmup_bars):]


def _robustness_metrics(
    rows: Sequence[HistoricalBar],
    *,
    base_config: ScalpingConfig,
    backtest_config: BacktestConfig,
    funding_points: Sequence[FundingPoint],
    trade_start_ms: int | None,
) -> RobustnessMetrics:
    expectations: list[float] = []
    robustness_rows = _robustness_rows(rows, trade_start_ms)
    for candidate in scalping_parameter_neighborhood(base_config):
        result = run_backtest(
            robustness_rows,
            backtest_config=backtest_config,
            funding_points=funding_points,
            engine=ScalpingSignalEngine(candidate),
            trade_start_ms=trade_start_ms,
        )
        expectations.append(result.metrics.expectancy)

    ordered = sorted(expectations)
    positive = sum(1 for value in expectations if value > 0)
    count = len(expectations)
    if count == 0:
        return RobustnessMetrics(0, 0, 0.0, 0.0, 0.0, 0.0)
    midpoint = count // 2
    if count % 2:
        median_value = ordered[midpoint]
    else:
        median_value = (ordered[midpoint - 1] + ordered[midpoint]) / 2.0
    return RobustnessMetrics(
        candidates=count,
        positive_oos_stress=positive,
        positive_fraction=positive / count,
        median_oos_stress_expectancy=median_value,
        worst_oos_stress_expectancy=min(ordered),
        best_oos_stress_expectancy=max(ordered),
    )


def run_scalping_validation(
    rows: Sequence[HistoricalBar],
    *,
    signal_config: ScalpingConfig | None = None,
    gate: ScalpingValidationGate | None = None,
    funding_points: Sequence[FundingPoint] = (),
    notional_usdt: float = 100.0,
) -> ScalpingValidationReport:
    """Run base + stressed friction replay and enforce a fail-closed gate.

    This does not certify profitability. It only prevents Demo progression when
    the supplied historical sample does not meet explicit minimum evidence.
    Historical Binance USD-M validation uses only features present in current
    public archives: closed-bar price action plus kline taker-flow. Book/spread
    microstructure is explicitly unavailable here and is validated forward in Demo.
    """

    sig_cfg = signal_config or ScalpingConfig()
    # Binance's current public USD-M archives do not provide a reliable current
    # historical bookTicker series. Historical validation therefore disables
    # unavailable book/spread features explicitly instead of synthesizing them.
    historical_sig_cfg = replace(sig_cfg, require_book_microstructure=False)
    gate_cfg = gate or ScalpingValidationGate()
    if not 0.10 <= gate_cfg.oos_fraction <= 0.50:
        raise ValueError("oos_fraction must be between 0.10 and 0.50")

    base_cfg = BacktestConfig(
        starting_equity=10_000.0,
        notional_usdt=notional_usdt,
        taker_fee_bps=5.0,
        assumed_spread_bps=1.5,
        slippage_bps=1.0,
        max_holding_bars=6,
        require_book=False,
        take_profit_enabled=True,
        tp_spacing_natr_multiplier=0.20,
        tp_min_spacing_bps=4.0,
        tp_max_spacing_bps=25.0,
        tp_steps=1.2,
        min_take_profit_bps=30.0,
    )
    stress_cfg = BacktestConfig(
        starting_equity=10_000.0,
        notional_usdt=notional_usdt,
        taker_fee_bps=7.0,
        assumed_spread_bps=3.0,
        slippage_bps=3.0,
        max_holding_bars=6,
        require_book=False,
        take_profit_enabled=True,
        tp_spacing_natr_multiplier=0.20,
        tp_min_spacing_bps=4.0,
        tp_max_spacing_bps=25.0,
        tp_steps=1.2,
        min_take_profit_bps=30.0,
    )

    base = run_backtest(
        rows,
        backtest_config=base_cfg,
        funding_points=funding_points,
        engine=ScalpingSignalEngine(historical_sig_cfg),
    )
    stress = run_backtest(
        rows,
        backtest_config=stress_cfg,
        funding_points=funding_points,
        engine=ScalpingSignalEngine(historical_sig_cfg),
    )

    ordered_rows = sorted(rows, key=lambda r: r.open_time_ms)
    oos_start_ms: int | None = None
    if ordered_rows:
        split_index = max(1, min(len(ordered_rows) - 1, int(len(ordered_rows) * (1.0 - gate_cfg.oos_fraction))))
        oos_start_ms = ordered_rows[split_index].open_time_ms

    oos_base = run_backtest(
        rows,
        backtest_config=base_cfg,
        funding_points=funding_points,
        engine=ScalpingSignalEngine(historical_sig_cfg),
        trade_start_ms=oos_start_ms,
    )
    oos_stress = run_backtest(
        rows,
        backtest_config=stress_cfg,
        funding_points=funding_points,
        engine=ScalpingSignalEngine(historical_sig_cfg),
        trade_start_ms=oos_start_ms,
    )

    robustness = _robustness_metrics(
        rows,
        base_config=historical_sig_cfg,
        backtest_config=stress_cfg,
        funding_points=funding_points,
        trade_start_ms=oos_start_ms,
    )

    reasons: list[str] = []
    history_span_days = 0.0
    if rows:
        ordered_times = sorted((r.open_time_ms, r.close_time_ms) for r in rows)
        history_span_days = max(
            0.0,
            (ordered_times[-1][1] - ordered_times[0][0]) / 86_400_000.0,
        )
    if history_span_days < gate_cfg.min_history_days:
        reasons.append(
            f"INSUFFICIENT_HISTORY:{history_span_days:.3f}<{gate_cfg.min_history_days:.3f}"
        )

    bm = base.metrics
    sm = stress.metrics
    om = oos_base.metrics
    osm = oos_stress.metrics

    if bm.trades < gate_cfg.min_trades:
        reasons.append(f"INSUFFICIENT_TRADES:{bm.trades}<{gate_cfg.min_trades}")
    if bm.expectancy <= gate_cfg.min_base_expectancy_usdt:
        reasons.append(
            f"BASE_EXPECTANCY:{bm.expectancy:.6f}<={gate_cfg.min_base_expectancy_usdt:.6f}"
        )
    if sm.expectancy <= gate_cfg.min_stress_expectancy_usdt:
        reasons.append(
            f"STRESS_EXPECTANCY:{sm.expectancy:.6f}<={gate_cfg.min_stress_expectancy_usdt:.6f}"
        )
    if bm.profit_factor < gate_cfg.min_base_profit_factor:
        reasons.append(
            f"BASE_PROFIT_FACTOR:{bm.profit_factor:.6f}<{gate_cfg.min_base_profit_factor:.6f}"
        )
    if bm.max_drawdown_pct > gate_cfg.max_base_drawdown_pct:
        reasons.append(
            f"BASE_DRAWDOWN:{bm.max_drawdown_pct:.6f}>{gate_cfg.max_base_drawdown_pct:.6f}"
        )
    if bm.cost_share_of_abs_gross > gate_cfg.max_base_cost_share:
        reasons.append(
            f"BASE_COST_SHARE:{bm.cost_share_of_abs_gross:.6f}>{gate_cfg.max_base_cost_share:.6f}"
        )
    if om.trades < gate_cfg.min_oos_trades:
        reasons.append(f"OOS_INSUFFICIENT_TRADES:{om.trades}<{gate_cfg.min_oos_trades}")
    if om.expectancy <= gate_cfg.min_oos_expectancy_usdt:
        reasons.append(
            f"OOS_EXPECTANCY:{om.expectancy:.6f}<={gate_cfg.min_oos_expectancy_usdt:.6f}"
        )
    if osm.expectancy <= gate_cfg.min_oos_stress_expectancy_usdt:
        reasons.append(
            f"OOS_STRESS_EXPECTANCY:{osm.expectancy:.6f}<={gate_cfg.min_oos_stress_expectancy_usdt:.6f}"
        )
    if om.profit_factor < gate_cfg.min_oos_profit_factor:
        reasons.append(
            f"OOS_PROFIT_FACTOR:{om.profit_factor:.6f}<{gate_cfg.min_oos_profit_factor:.6f}"
        )
    if robustness.positive_fraction < gate_cfg.min_robust_positive_fraction:
        reasons.append(
            "ROBUSTNESS_POSITIVE_FRACTION:"
            f"{robustness.positive_fraction:.6f}<{gate_cfg.min_robust_positive_fraction:.6f}"
        )

    return ScalpingValidationReport(
        base=base,
        stress=stress,
        oos_base=oos_base,
        oos_stress=oos_stress,
        by_setup=_slice_metrics(base.trades, "setup"),
        by_regime=_slice_metrics(base.trades, "regime"),
        history_span_days=history_span_days,
        oos_start_ms=oos_start_ms,
        historical_microstructure_scope="PRICE_ACTION_TAKER_ONLY",
        robustness=robustness,
        passed=not reasons,
        reasons=tuple(reasons),
    )
