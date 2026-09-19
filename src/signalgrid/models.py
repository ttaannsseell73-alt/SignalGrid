from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from time import time

class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    PASS = "PASS"


class GridMode(str, Enum):
    LONG_GRID = "LONG_GRID"
    SHORT_GRID = "SHORT_GRID"
    NEUTRAL_GRID = "NEUTRAL_GRID"
    PASS = "PASS"


@dataclass(slots=True)
class Signal:
    symbol: str
    direction: Direction
    strength: float
    regime: str
    setup: str
    invalidation: float | None
    liquidity_ok: bool
    expires_at: float
    created_at: float
    grid_mode: GridMode | None = None

    def __post_init__(self) -> None:
        if self.grid_mode is None:
            if self.direction is Direction.LONG:
                self.grid_mode = GridMode.LONG_GRID
            elif self.direction is Direction.SHORT:
                self.grid_mode = GridMode.SHORT_GRID
            else:
                self.grid_mode = GridMode.PASS

    @classmethod
    def pass_signal(cls, symbol: str, reason: str = "NO_EDGE") -> "Signal":
        now = time()
        return cls(symbol, Direction.PASS, 0.0, "NONE", reason, None, True, now, now, GridMode.PASS)

    @property
    def expired(self) -> bool:
        return time() >= self.expires_at

@dataclass(slots=True)
class RiskDecision:
    approved: bool
    reason: str
    notional_usdt: float = 0.0
    leverage: int = 1
