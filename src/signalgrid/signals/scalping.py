from __future__ import annotations

from dataclasses import dataclass
from time import time

from signalgrid.market.state import Bar, SymbolState
from signalgrid.models import Direction, GridMode, Signal
from signalgrid.signals.flow import book_imbalance, spread_bps, taker_imbalance
from signalgrid.signals.volatility import natr, volatility_expansion


SCALPING_PROFILE_VERSION = "SCALPING_V1_20260919"


@dataclass(frozen=True, slots=True)
class ScalpingStructure:
    direction: int
    setup: str
    invalidation: float | None
    trigger_level: float | None


@dataclass(frozen=True, slots=True)
class MarketStructure:
    bias: int
    label: str


@dataclass(slots=True)
class ScalpingConfig:
    structure_lookback: int = 12
    swing_window: int = 5
    compression_lookback: int = 8
    compression_baseline: int = 30
    compression_ratio_max: float = 0.78
    retest_tolerance_bps: float = 8.0
    max_spread_bps: float = 4.0
    min_natr_bps: float = 4.0
    max_natr_bps: float = 180.0
    min_expansion: float = 0.90
    strong_expansion: float = 1.35
    min_directional_flow: float = 0.03
    absorption_flow_threshold: float = 0.35
    min_flow_price_progress_natr: float = 0.08
    min_score: float = 0.62
    min_stop_bps: float = 3.0
    max_stop_bps: float = 120.0
    ttl_seconds: float = 3.0


def _avg_range(bars: list[Bar]) -> float:
    if not bars:
        return 0.0
    return sum(max(0.0, b.high - b.low) for b in bars) / len(bars)


def compression_ratio(
    bars: list[Bar],
    short: int = 8,
    baseline: int = 30,
) -> float | None:
    if short <= 0 or baseline <= short or len(bars) < baseline + 1:
        return None
    prior = bars[:-1]
    short_avg = _avg_range(prior[-short:])
    base_avg = _avg_range(prior[-baseline:])
    if base_avg <= 0:
        return None
    return short_avg / base_avg


def market_structure_bias(bars: list[Bar], window: int = 5) -> MarketStructure:
    """Quantify HH/HL vs LH/LL using two adjacent closed-bar windows."""
    if window < 2 or len(bars) < (2 * window + 1):
        return MarketStructure(0, "STRUCTURE_WARMUP")
    prior = bars[:-1]
    older = prior[-2 * window:-window]
    recent = prior[-window:]
    older_hi = max(b.high for b in older)
    older_lo = min(b.low for b in older)
    recent_hi = max(b.high for b in recent)
    recent_lo = min(b.low for b in recent)

    if recent_hi > older_hi and recent_lo > older_lo:
        return MarketStructure(+1, "HH_HL")
    if recent_hi < older_hi and recent_lo < older_lo:
        return MarketStructure(-1, "LH_LL")
    return MarketStructure(0, "RANGE_TRANSITION")


def price_impact_efficiency(
    bars: list[Bar],
    reference: float,
    side: int,
    natr_value: float,
    taker_flow: float,
) -> float | None:
    """Signed price progress per unit of same-direction taker pressure.

    Low efficiency under strong aggressive flow is an absorption warning.
    """
    if len(bars) < 2 or reference <= 0 or natr_value <= 0 or side not in (-1, 1):
        return None
    prior_close = bars[-2].close
    if prior_close <= 0:
        return None
    signed_progress_natr = (
        side * (reference - prior_close) / max(prior_close * natr_value, 1e-12)
    )
    directional_taker = max(0.0, side * taker_flow)
    return signed_progress_natr / max(directional_taker, 0.05)


def detect_scalping_structure(
    bars: list[Bar],
    lookback: int = 12,
    retest_tolerance_bps: float = 8.0,
) -> ScalpingStructure:
    if lookback < 3 or len(bars) < lookback + 2:
        return ScalpingStructure(0, "WARMUP", None, None)

    cur = bars[-1]
    prev = bars[-2]
    prior = bars[-(lookback + 2):-2]
    hi = max(b.high for b in prior)
    lo = min(b.low for b in prior)
    tolerance = retest_tolerance_bps / 10_000.0

    # Breakout on the previous closed bar, followed by a controlled retest that
    # still closes on the breakout side. No future bar is inspected.
    if (
        prev.close > hi
        and cur.low <= hi * (1.0 + tolerance)
        and cur.close > hi
        and cur.close >= cur.open
    ):
        return ScalpingStructure(+1, "BREAKOUT_RETEST", min(cur.low, hi), hi)

    if (
        prev.close < lo
        and cur.high >= lo * (1.0 - tolerance)
        and cur.close < lo
        and cur.close <= cur.open
    ):
        return ScalpingStructure(-1, "BREAKOUT_RETEST", max(cur.high, lo), lo)

    # Liquidity sweep / failed breakout: probe outside the prior range and
    # close back inside it.
    if cur.low < lo and cur.close > lo:
        return ScalpingStructure(+1, "LIQUIDITY_SWEEP_REJECTION", cur.low, lo)

    if cur.high > hi and cur.close < hi:
        return ScalpingStructure(-1, "LIQUIDITY_SWEEP_REJECTION", cur.high, hi)

    if cur.close > hi:
        return ScalpingStructure(+1, "BREAKOUT_ACCEPTANCE", hi, hi)

    if cur.close < lo:
        return ScalpingStructure(-1, "BREAKOUT_ACCEPTANCE", lo, lo)

    return ScalpingStructure(0, "NO_SCALP_STRUCTURE", None, None)


class ScalpingSignalEngine:
    """Directional, cost-aware scalp selector.

    The engine deliberately emits no neutral grid signal. Grid logic remains an
    execution mechanism and only runs after a directional scalp setup survives
    structure, liquidity, volatility, flow and stop-distance gates.
    """

    def __init__(self, cfg: ScalpingConfig | None = None):
        self.cfg = cfg or ScalpingConfig()

    def evaluate(self, state: SymbolState) -> Signal:
        bars = list(state.bars)
        nv = natr(bars)
        vx = volatility_expansion(bars)
        spr = spread_bps(state.best_bid, state.best_ask)

        if nv is None or vx is None:
            return Signal.pass_signal(state.symbol, "SCALP_WARMUP")
        if spr is None or spr > self.cfg.max_spread_bps:
            return Signal.pass_signal(state.symbol, "SCALP_LIQUIDITY_GATE")

        natr_bps = nv * 10_000.0
        if natr_bps < self.cfg.min_natr_bps:
            return Signal.pass_signal(state.symbol, "SCALP_VOL_TOO_LOW")
        if natr_bps > self.cfg.max_natr_bps:
            return Signal.pass_signal(state.symbol, "SCALP_VOL_TOO_HIGH")

        structure = detect_scalping_structure(
            bars,
            self.cfg.structure_lookback,
            self.cfg.retest_tolerance_bps,
        )
        if structure.direction == 0:
            return Signal.pass_signal(state.symbol, structure.setup)

        side = structure.direction
        reference = self._reference_price(state, bars)
        if reference is None or structure.invalidation is None or structure.invalidation <= 0:
            return Signal.pass_signal(state.symbol, "SCALP_NO_INVALIDATION")

        if side > 0:
            if structure.invalidation >= reference:
                return Signal.pass_signal(state.symbol, "SCALP_INVALIDATION_WRONG_SIDE")
            stop_bps = (reference - structure.invalidation) / reference * 10_000.0
        else:
            if structure.invalidation <= reference:
                return Signal.pass_signal(state.symbol, "SCALP_INVALIDATION_WRONG_SIDE")
            stop_bps = (structure.invalidation - reference) / reference * 10_000.0

        if stop_bps < self.cfg.min_stop_bps:
            return Signal.pass_signal(state.symbol, "SCALP_STOP_TOO_CLOSE")
        if stop_bps > self.cfg.max_stop_bps:
            return Signal.pass_signal(state.symbol, "SCALP_STOP_TOO_WIDE")

        ti = taker_imbalance(state.taker_buy_quote, state.taker_sell_quote)
        bi = book_imbalance(state.bid_depth, state.ask_depth)
        directional_flow = side * (0.65 * ti + 0.35 * bi)
        if directional_flow < self.cfg.min_directional_flow:
            return Signal.pass_signal(state.symbol, "SCALP_FLOW_NOT_CONFIRMED")

        market_structure = market_structure_bias(bars, self.cfg.swing_window)
        if (
            structure.setup in {"BREAKOUT_ACCEPTANCE", "BREAKOUT_RETEST"}
            and market_structure.bias not in (0, side)
        ):
            return Signal.pass_signal(state.symbol, "SCALP_STRUCTURE_CONFLICT")

        impact_efficiency = price_impact_efficiency(bars, reference, side, nv, ti)
        if (
            side * ti >= self.cfg.absorption_flow_threshold
            and impact_efficiency is not None
            and impact_efficiency < self.cfg.min_flow_price_progress_natr
        ):
            return Signal.pass_signal(state.symbol, "SCALP_ABSORPTION")

        compression = compression_ratio(
            bars,
            self.cfg.compression_lookback,
            self.cfg.compression_baseline,
        )
        setup = structure.setup
        if (
            setup == "BREAKOUT_ACCEPTANCE"
            and compression is not None
            and compression <= self.cfg.compression_ratio_max
        ):
            setup = "COMPRESSION_BREAKOUT"

        # Sweeps/retests can work before broad volatility expansion; raw
        # breakout acceptance must show at least modest expansion.
        if setup in {"BREAKOUT_ACCEPTANCE", "COMPRESSION_BREAKOUT"} and vx < self.cfg.min_expansion:
            return Signal.pass_signal(state.symbol, "SCALP_NO_EXPANSION")

        structure_score = {
            "BREAKOUT_RETEST": 1.0,
            "LIQUIDITY_SWEEP_REJECTION": 0.95,
            "COMPRESSION_BREAKOUT": 0.92,
            "BREAKOUT_ACCEPTANCE": 0.80,
        }.get(setup, 0.0)

        flow_score = min(1.0, max(0.0, directional_flow) / 0.35)
        context_score = 1.0 if market_structure.bias == side else (0.65 if market_structure.bias == 0 else 0.35)
        impact_score = 0.5 if impact_efficiency is None else min(1.0, max(0.0, impact_efficiency) / 0.50)
        expansion_score = min(
            1.0,
            max(0.0, (vx - self.cfg.min_expansion) / max(0.05, self.cfg.strong_expansion - self.cfg.min_expansion)),
        )
        if setup in {"BREAKOUT_RETEST", "LIQUIDITY_SWEEP_REJECTION"}:
            expansion_score = max(0.45, expansion_score)

        spread_score = 1.0 - min(1.0, spr / max(self.cfg.max_spread_bps, 1e-9))
        stop_quality = 1.0 - min(
            1.0,
            max(0.0, stop_bps - self.cfg.min_stop_bps)
            / max(self.cfg.max_stop_bps - self.cfg.min_stop_bps, 1e-9),
        )

        strength = (
            0.30 * structure_score
            + 0.22 * flow_score
            + 0.14 * expansion_score
            + 0.10 * spread_score
            + 0.09 * stop_quality
            + 0.10 * context_score
            + 0.05 * impact_score
        )
        if strength < self.cfg.min_score:
            return Signal.pass_signal(state.symbol, "SCALP_LOW_SCORE")

        now = time()
        direction = Direction.LONG if side > 0 else Direction.SHORT
        return Signal(
            symbol=state.symbol,
            direction=direction,
            strength=round(strength, 4),
            regime=(
                "SCALP_TREND_EXPANSION"
                if vx >= self.cfg.min_expansion and market_structure.bias == side
                else "SCALP_EXPANSION"
                if vx >= self.cfg.min_expansion
                else "SCALP_REVERSAL"
            ),
            setup=setup,
            invalidation=structure.invalidation,
            liquidity_ok=True,
            expires_at=now + self.cfg.ttl_seconds,
            created_at=now,
            grid_mode=GridMode.LONG_GRID if direction is Direction.LONG else GridMode.SHORT_GRID,
        )

    @staticmethod
    def _reference_price(state: SymbolState, bars: list[Bar]) -> float | None:
        if (
            state.best_bid is not None
            and state.best_ask is not None
            and state.best_bid > 0
            and state.best_ask >= state.best_bid
        ):
            return (state.best_bid + state.best_ask) / 2.0
        if bars and bars[-1].close > 0:
            return bars[-1].close
        return None
