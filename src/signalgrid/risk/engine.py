from __future__ import annotations
from dataclasses import dataclass
from signalgrid.models import Direction, GridMode, RiskDecision, Signal

@dataclass(slots=True)
class PositionView:
    symbol: str
    direction: Direction
    notional_usdt: float

@dataclass(slots=True)
class RiskConfig:
    max_positions: int = 10
    max_total_notional_usdt: float = 10_000.0
    base_notional_usdt: float = 250.0
    max_notional_per_trade_usdt: float = 750.0
    leverage: int = 3

class RiskEngine:
    def __init__(self, cfg: RiskConfig | None = None):
        self.cfg = cfg or RiskConfig()

    def decide(self, signal: Signal, positions: list[PositionView]) -> RiskDecision:
        if signal.grid_mode is GridMode.PASS or signal.expired:
            return RiskDecision(False, "NO_VALID_SIGNAL")
        if len(positions) >= self.cfg.max_positions:
            return RiskDecision(False, "MAX_POSITIONS")
        if any(p.symbol == signal.symbol for p in positions):
            return RiskDecision(False, "SYMBOL_ALREADY_ACTIVE")
        used = sum(abs(p.notional_usdt) for p in positions)
        room = self.cfg.max_total_notional_usdt - used
        if room <= 0:
            return RiskDecision(False, "NO_NOTIONAL_ROOM")
        scaled = self.cfg.base_notional_usdt * (0.75 + signal.strength)
        size = min(scaled, self.cfg.max_notional_per_trade_usdt, room)
        if size <= 0:
            return RiskDecision(False, "SIZE_ZERO")
        return RiskDecision(True, "APPROVED", round(size, 2), self.cfg.leverage)
