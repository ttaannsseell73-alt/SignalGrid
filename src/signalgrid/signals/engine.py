from __future__ import annotations
from dataclasses import dataclass
from time import time
from signalgrid.market.state import SymbolState
from signalgrid.models import Direction, Signal
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
            return Signal.pass_signal(state.symbol, "NO_STRUCTURE")
        if vx < self.cfg.min_expansion:
            return Signal.pass_signal(state.symbol, "NO_VOL_EXPANSION")

        side = structure.direction
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
        return Signal(
            symbol=state.symbol,
            direction=Direction.LONG if side > 0 else Direction.SHORT,
            strength=round(strength, 4),
            regime="EXPANSION" if vx < self.cfg.strong_expansion else "HIGH_EXPANSION",
            setup=structure.setup,
            invalidation=structure.invalidation,
            liquidity_ok=True,
            expires_at=now + self.cfg.ttl_seconds,
            created_at=now,
        )
