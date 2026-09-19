from __future__ import annotations

from dataclasses import dataclass
from math import inf
from statistics import median
from typing import Iterable, Protocol, Sequence

from signalgrid.backtest.data import FundingPoint, HistoricalBar
from signalgrid.market.state import SymbolState
from signalgrid.models import Direction, Signal
from signalgrid.signals.engine import SignalConfig, SignalEngine


class BacktestDataQualityError(ValueError):
    pass


class SignalEvaluator(Protocol):
    def evaluate(self, state: SymbolState) -> Signal: ...


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    starting_equity: float = 10_000.0
    notional_usdt: float = 1_000.0
    taker_fee_bps: float = 5.0
    assumed_spread_bps: float = 2.0
    slippage_bps: float = 1.0
    max_holding_bars: int = 12
    require_book: bool = True


@dataclass(frozen=True, slots=True)
class SimulatedTrade:
    symbol: str
    direction: Direction
    setup: str
    signal_time_ms: int
    entry_time_ms: int
    exit_time_ms: int
    entry_price: float
    exit_price: float
    quantity: float
    gross_pnl: float
    fees: float
    funding_cost: float
    net_pnl: float
    exit_reason: str
    holding_bars: int
    mae_usdt: float = 0.0
    mfe_usdt: float = 0.0


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    trades: int
    wins: int
    losses: int
    win_rate: float
    gross_pnl: float
    net_pnl: float
    fees: float
    funding_cost: float
    expectancy: float
    profit_factor: float
    max_drawdown_pct: float
    turnover_usdt: float
    cost_share_of_abs_gross: float
    avg_holding_bars: float = 0.0
    avg_mae_usdt: float = 0.0
    avg_mfe_usdt: float = 0.0


@dataclass(frozen=True, slots=True)
class BacktestResult:
    symbol: str
    trades: tuple[SimulatedTrade, ...]
    metrics: BacktestMetrics
    skipped_invalidated_before_entry: int = 0


def _side(direction: Direction) -> int:
    if direction is Direction.LONG:
        return 1
    if direction is Direction.SHORT:
        return -1
    raise ValueError("PASS has no execution side")


def _friction_fraction(cfg: BacktestConfig) -> float:
    return (cfg.assumed_spread_bps / 2.0 + cfg.slippage_bps) / 10_000.0


def _entry_price(base: float, direction: Direction, cfg: BacktestConfig) -> float:
    return base * (1.0 + _side(direction) * _friction_fraction(cfg))


def _exit_price(base: float, direction: Direction, cfg: BacktestConfig) -> float:
    return base * (1.0 - _side(direction) * _friction_fraction(cfg))


def _funding_cost(points: Sequence[FundingPoint], symbol: str, direction: Direction, entry_ms: int, exit_ms: int, notional: float) -> float:
    sign = _side(direction)
    rate_sum = sum(p.rate for p in points if p.symbol.upper() == symbol.upper() and entry_ms < p.timestamp_ms <= exit_ms)
    return notional * sign * rate_sum


def _stop_hit(row: HistoricalBar, direction: Direction, invalidation: float | None) -> bool:
    if invalidation is None:
        return False
    return row.low <= invalidation if direction is Direction.LONG else row.high >= invalidation


def _invalid_before_entry(row: HistoricalBar, signal: Signal) -> bool:
    if signal.invalidation is None:
        return False
    if signal.direction is Direction.LONG:
        return row.open <= signal.invalidation
    if signal.direction is Direction.SHORT:
        return row.open >= signal.invalidation
    return True


def metrics_from_trades(trades: Sequence[SimulatedTrade], starting_equity: float) -> BacktestMetrics:
    net = [t.net_pnl for t in trades]
    wins = sum(1 for p in net if p > 0)
    losses = sum(1 for p in net if p < 0)
    gross_profit = sum(p for p in net if p > 0)
    gross_loss = -sum(p for p in net if p < 0)
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (inf if gross_profit > 0 else 0.0)
    equity = starting_equity
    peak = starting_equity
    max_dd = 0.0
    for pnl in net:
        equity += pnl
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)
    gross_pnl = sum(t.gross_pnl for t in trades)
    fees = sum(t.fees for t in trades)
    funding = sum(t.funding_cost for t in trades)
    net_pnl = sum(net)
    turnover = sum(abs(t.quantity * t.entry_price) + abs(t.quantity * t.exit_price) for t in trades)
    costs = fees + funding
    return BacktestMetrics(
        trades=len(trades), wins=wins, losses=losses,
        win_rate=wins / len(trades) if trades else 0.0,
        gross_pnl=gross_pnl, net_pnl=net_pnl, fees=fees, funding_cost=funding,
        expectancy=net_pnl / len(trades) if trades else 0.0,
        profit_factor=profit_factor,
        max_drawdown_pct=max_dd,
        turnover_usdt=turnover,
        cost_share_of_abs_gross=abs(costs) / max(abs(gross_pnl), 1e-12),
        avg_holding_bars=sum(t.holding_bars for t in trades) / len(trades) if trades else 0.0,
        avg_mae_usdt=sum(t.mae_usdt for t in trades) / len(trades) if trades else 0.0,
        avg_mfe_usdt=sum(t.mfe_usdt for t in trades) / len(trades) if trades else 0.0,
    )


def run_backtest(
    rows: Sequence[HistoricalBar],
    *,
    signal_config: SignalConfig | None = None,
    backtest_config: BacktestConfig | None = None,
    funding_points: Sequence[FundingPoint] = (),
    engine: SignalEvaluator | None = None,
    trade_start_ms: int | None = None,
    trade_end_ms: int | None = None,
) -> BacktestResult:
    cfg = backtest_config or BacktestConfig()
    if cfg.max_holding_bars < 1 or cfg.notional_usdt <= 0:
        raise ValueError("invalid backtest configuration")
    if not rows:
        return BacktestResult("", (), metrics_from_trades((), cfg.starting_equity))
    ordered = sorted(rows, key=lambda r: r.open_time_ms)
    symbol = ordered[0].symbol.upper()
    if any(r.symbol.upper() != symbol for r in ordered):
        raise BacktestDataQualityError("run_backtest accepts one symbol at a time")
    if any(b.open_time_ms >= b.close_time_ms for b in ordered):
        raise BacktestDataQualityError("bar open_time must be before close_time")
    if any(b.open_time_ms <= a.open_time_ms for a, b in zip(ordered, ordered[1:])):
        raise BacktestDataQualityError("bars must have unique increasing timestamps")
    if cfg.require_book and any(not r.has_book for r in ordered):
        raise BacktestDataQualityError("full-core backtest requires historical book snapshot data; synthetic book data is forbidden")

    evaluator = engine or SignalEngine(signal_config)
    state = SymbolState(symbol)
    trades: list[SimulatedTrade] = []
    pending: tuple[Signal, HistoricalBar] | None = None
    position: dict[str, object] | None = None
    skipped = 0
    fee_rate = cfg.taker_fee_bps / 10_000.0

    for idx, row in enumerate(ordered):
        # 1) Orders decided on the prior bar may execute only now, at this bar's open.
        if position is None and pending is not None:
            sig, signal_row = pending
            pending = None
            in_window = (trade_start_ms is None or row.open_time_ms >= trade_start_ms) and (trade_end_ms is None or row.open_time_ms < trade_end_ms)
            if in_window:
                if _invalid_before_entry(row, sig):
                    skipped += 1
                else:
                    px = _entry_price(row.open, sig.direction, cfg)
                    qty = cfg.notional_usdt / px
                    position = {
                        "signal": sig, "signal_time_ms": signal_row.close_time_ms,
                        "entry_index": idx, "entry_time_ms": row.open_time_ms,
                        "entry_price": px, "qty": qty,
                        "mae_usdt": 0.0, "mfe_usdt": 0.0,
                    }

        # 2) Existing position sees this bar only after it has opened.
        if position is not None:
            sig = position["signal"]
            assert isinstance(sig, Signal)
            entry_idx = int(position["entry_index"])
            entry_price_for_excursion = float(position["entry_price"])
            qty_for_excursion = float(position["qty"])
            if sig.direction is Direction.LONG:
                favorable = max(0.0, (row.high - entry_price_for_excursion) * qty_for_excursion)
                adverse = max(0.0, (entry_price_for_excursion - row.low) * qty_for_excursion)
            else:
                favorable = max(0.0, (entry_price_for_excursion - row.low) * qty_for_excursion)
                adverse = max(0.0, (row.high - entry_price_for_excursion) * qty_for_excursion)
            position["mfe_usdt"] = max(float(position["mfe_usdt"]), favorable)
            position["mae_usdt"] = max(float(position["mae_usdt"]), adverse)
            exit_reason: str | None = None
            exit_base: float | None = None
            if _stop_hit(row, sig.direction, sig.invalidation):
                exit_reason = "INVALIDATION_STOP"
                exit_base = float(sig.invalidation)
            elif idx - entry_idx + 1 >= cfg.max_holding_bars:
                exit_reason = "MAX_HOLD"
                exit_base = row.close
            elif trade_end_ms is not None and row.close_time_ms >= trade_end_ms:
                exit_reason = "WINDOW_END"
                exit_base = row.close
            if exit_reason is not None and exit_base is not None:
                entry_price = float(position["entry_price"])
                qty = float(position["qty"])
                exit_price = _exit_price(exit_base, sig.direction, cfg)
                gross = qty * (exit_price - entry_price) * _side(sig.direction)
                entry_fee = abs(qty * entry_price) * fee_rate
                exit_fee = abs(qty * exit_price) * fee_rate
                funding = _funding_cost(funding_points, symbol, sig.direction, int(position["entry_time_ms"]), row.close_time_ms, cfg.notional_usdt)
                fees = entry_fee + exit_fee
                trades.append(SimulatedTrade(
                    symbol=symbol, direction=sig.direction, setup=sig.setup,
                    signal_time_ms=int(position["signal_time_ms"]), entry_time_ms=int(position["entry_time_ms"]), exit_time_ms=row.close_time_ms,
                    entry_price=entry_price, exit_price=exit_price, quantity=qty,
                    gross_pnl=gross, fees=fees, funding_cost=funding,
                    net_pnl=gross - fees - funding, exit_reason=exit_reason,
                    holding_bars=idx - entry_idx + 1,
                    mae_usdt=float(position["mae_usdt"]),
                    mfe_usdt=float(position["mfe_usdt"]),
                ))
                position = None

        # 3) The current bar is now complete. Only now does SignalEngine see its OHLC/flow/book state.
        state.add_bar(row.as_bar())
        state.taker_buy_quote = row.taker_buy_quote
        state.taker_sell_quote = row.taker_sell_quote
        state.flow_bucket_start_ms = row.open_time_ms
        state.best_bid = row.best_bid
        state.best_ask = row.best_ask
        state.bid_depth = float(row.bid_depth or 0.0)
        state.ask_depth = float(row.ask_depth or 0.0)
        state.last_event_time_ms = row.close_time_ms
        signal = evaluator.evaluate(state)
        if position is None and pending is None and signal.direction is not Direction.PASS and idx + 1 < len(ordered):
            pending = (signal, row)

    # Deterministic forced close for any open trade at the final known close.
    if position is not None:
        row = ordered[-1]
        sig = position["signal"]
        assert isinstance(sig, Signal)
        entry_price = float(position["entry_price"])
        qty = float(position["qty"])
        exit_price = _exit_price(row.close, sig.direction, cfg)
        gross = qty * (exit_price - entry_price) * _side(sig.direction)
        fees = abs(qty * entry_price) * fee_rate + abs(qty * exit_price) * fee_rate
        funding = _funding_cost(funding_points, symbol, sig.direction, int(position["entry_time_ms"]), row.close_time_ms, cfg.notional_usdt)
        trades.append(SimulatedTrade(
            symbol=symbol, direction=sig.direction, setup=sig.setup,
            signal_time_ms=int(position["signal_time_ms"]), entry_time_ms=int(position["entry_time_ms"]), exit_time_ms=row.close_time_ms,
            entry_price=entry_price, exit_price=exit_price, quantity=qty,
            gross_pnl=gross, fees=fees, funding_cost=funding, net_pnl=gross-fees-funding,
            exit_reason="DATA_END", holding_bars=len(ordered)-int(position["entry_index"]),
            mae_usdt=float(position["mae_usdt"]),
            mfe_usdt=float(position["mfe_usdt"]),
        ))

    return BacktestResult(symbol, tuple(trades), metrics_from_trades(trades, cfg.starting_equity), skipped)


@dataclass(frozen=True, slots=True)
class StabilityReport:
    candidates: int
    profitable_candidates: int
    positive_fraction: float
    median_expectancy: float
    worst_expectancy: float
    best_expectancy: float
    stable: bool


def parameter_stability(
    rows: Sequence[HistoricalBar],
    candidates: Sequence[SignalConfig],
    *,
    backtest_config: BacktestConfig | None = None,
    funding_points: Sequence[FundingPoint] = (),
    min_positive_fraction: float = 0.60,
) -> StabilityReport:
    if not candidates:
        raise ValueError("at least one candidate is required")
    exps = [run_backtest(rows, signal_config=c, backtest_config=backtest_config, funding_points=funding_points).metrics.expectancy for c in candidates]
    profitable = sum(1 for x in exps if x > 0)
    frac = profitable / len(exps)
    return StabilityReport(len(exps), profitable, frac, median(exps), min(exps), max(exps), frac >= min_positive_fraction)
