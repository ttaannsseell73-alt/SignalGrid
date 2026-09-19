from __future__ import annotations
from dataclasses import dataclass
from time import time
from signalgrid.market.state import SymbolState
from signalgrid.models import Direction, GridMode, Signal
from signalgrid.signals.flow import book_imbalance, spread_bps, taker_imbalance
from signalgrid.signals.structure import detect_structure
from signalgrid.signals.volatility import natr, volatility_expansion

@dataclass(slots=True)
class SignalConfig:
    structure_lookback: int = 20
    max_spread_bps: float = 8.0
    min_expansion: float = 1.05
    strong_expansion: float = 1.35
    min_flow_abs: float = 0.08
    min_book_abs: float = 0.05
    entry_threshold: float = 0.60
    min_directional_stop_bps: float = 3.0
    neutral_enabled: bool = True
    neutral_max_expansion: float = 1.00
    neutral_max_flow_abs: float = 0.12
    neutral_max_book_abs: float = 0.12
    neutral_entry_threshold: float = 0.55
    ttl_seconds: float = 20.0

class SignalEngine:
    def __init__(self, cfg: SignalConfig | None = None):
        self.cfg = cfg or SignalConfig()

    def evaluate(self, state: SymbolState) -> Signal:
        bars = list(state.bars)
        nv = natr(bars)
        vx = volatility_expansion(bars)
        structure = detect_structure(bars, self.cfg.structure_lookback)
        ti = taker_imbalance(state.taker_buy_quote, state.taker_sell_quote)
        bi = book_imbalance(state.bid_depth, state.ask_depth)
        spr = spread_bps(state.best_bid, state.best_ask)
        if nv is None or vx is None:
            return Signal.pass_signal(state.symbol, "WARMUP")
        if spr is None or spr > self.cfg.max_spread_bps:
            return Signal.pass_signal(state.symbol, "LIQUIDITY_GATE")
        if structure.direction == 0:
            if not self.cfg.neutral_enabled:
                return Signal.pass_signal(state.symbol, "NO_STRUCTURE")
            if vx > self.cfg.neutral_max_expansion:
                return Signal.pass_signal(state.symbol, "RANGE_TOO_VOLATILE")
            if abs(ti) > self.cfg.neutral_max_flow_abs:
                return Signal.pass_signal(state.symbol, "RANGE_FLOW_IMBALANCE")
            if abs(bi) > self.cfg.neutral_max_book_abs:
                return Signal.pass_signal(state.symbol, "RANGE_BOOK_IMBALANCE")
            compression_score = min(
                1.0,
                max(
                    0.0,
                    (self.cfg.neutral_max_expansion - vx)
                    / max(0.05, self.cfg.neutral_max_expansion - 0.70),
                ),
            )
            flow_balance = 1.0 - min(1.0, abs(ti) / max(self.cfg.neutral_max_flow_abs, 1e-9))
            book_balance = 1.0 - min(1.0, abs(bi) / max(self.cfg.neutral_max_book_abs, 1e-9))
            strength = 0.45 + 0.25 * compression_score + 0.20 * flow_balance + 0.10 * book_balance
            if strength < self.cfg.neutral_entry_threshold:
                return Signal.pass_signal(state.symbol, "RANGE_LOW_SCORE")
            now = time()
            return Signal(
                symbol=state.symbol,
                direction=Direction.PASS,
                strength=round(strength, 4),
                regime="RANGE",
                setup="RANGE_NEUTRAL",
                invalidation=None,
                liquidity_ok=True,
                expires_at=now + self.cfg.ttl_seconds,
                created_at=now,
                grid_mode=GridMode.NEUTRAL_GRID,
            )
        if vx < self.cfg.min_expansion:
            return Signal.pass_signal(state.symbol, "NO_VOL_EXPANSION")
        reference = None
        if (
            state.best_bid is not None
            and state.best_ask is not None
            and state.best_bid > 0
            and state.best_ask >= state.best_bid
        ):
            reference = (state.best_bid + state.best_ask) / 2.0
        elif bars and bars[-1].close > 0:
            reference = bars[-1].close
        if reference is None or structure.invalidation is None or structure.invalidation <= 0:
            return Signal.pass_signal(state.symbol, "NO_INVALIDATION")
        side = structure.direction
        if side > 0:
            if structure.invalidation >= reference:
                return Signal.pass_signal(state.symbol, "INVALIDATION_WRONG_SIDE")
            stop_distance_bps = (reference - structure.invalidation) / reference * 10_000.0
        else:
            if structure.invalidation <= reference:
                return Signal.pass_signal(state.symbol, "INVALIDATION_WRONG_SIDE")
            stop_distance_bps = (structure.invalidation - reference) / reference * 10_000.0
        if stop_distance_bps < self.cfg.min_directional_stop_bps:
            return Signal.pass_signal(state.symbol, "STOP_TOO_CLOSE")
        flow_support = max(0.0, side * ti)
        book_support = max(0.0, side * bi)
        struct_score = 1.0
        vol_score = min(1.0, max(0.0, (vx - self.cfg.min_expansion) / max(0.01, self.cfg.strong_expansion - self.cfg.min_expansion)))
        flow_score = min(1.0, flow_support / max(self.cfg.min_flow_abs, 1e-9))
        book_score = min(1.0, book_support / max(self.cfg.min_book_abs, 1e-9))
        strength = 0.35 * struct_score + 0.25 * vol_score + 0.25 * flow_score + 0.15 * book_score
        if strength < self.cfg.entry_threshold:
            return Signal.pass_signal(state.symbol, "LOW_SCORE")
        now = time()
        direction = Direction.LONG if side > 0 else Direction.SHORT
        return Signal(
            symbol=state.symbol,
            direction=direction,
            strength=round(strength, 4),
            regime="EXPANSION" if vx < self.cfg.strong_expansion else "HIGH_EXPANSION",
            setup=structure.setup,
            invalidation=structure.invalidation,
            liquidity_ok=True,
            expires_at=now + self.cfg.ttl_seconds,
            created_at=now,
            grid_mode=GridMode.LONG_GRID if direction is Direction.LONG else GridMode.SHORT_GRID,
        )
