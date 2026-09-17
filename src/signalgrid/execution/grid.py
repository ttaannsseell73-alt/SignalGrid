from __future__ import annotations

from dataclasses import dataclass
from hashlib import blake2s

from signalgrid.market.state import SymbolState
from signalgrid.models import Direction, RiskDecision, Signal
from signalgrid.signals.volatility import natr


class GridPlanError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class GridConfig:
    entry_levels: int = 4
    starter_fraction: float = 0.40
    spacing_natr_multiplier: float = 0.35
    min_spacing_bps: float = 15.0
    max_spacing_bps: float = 120.0
    take_profit_steps: float = 1.5

    def __post_init__(self) -> None:
        if not 2 <= self.entry_levels <= 6:
            raise ValueError("entry_levels must be between 2 and 6")
        if not 0.0 < self.starter_fraction < 1.0:
            raise ValueError("starter_fraction must be between 0 and 1")
        if self.spacing_natr_multiplier <= 0:
            raise ValueError("spacing_natr_multiplier must be positive")
        if self.min_spacing_bps <= 0 or self.max_spacing_bps < self.min_spacing_bps:
            raise ValueError("invalid spacing bounds")
        if self.take_profit_steps <= 0:
            raise ValueError("take_profit_steps must be positive")


@dataclass(frozen=True, slots=True)
class GridEntryLevel:
    index: int
    kind: str
    price: float
    notional_usdt: float


@dataclass(frozen=True, slots=True)
class GridPlan:
    campaign_id: str
    symbol: str
    direction: Direction
    reference_price: float
    total_notional_usdt: float
    leverage: int
    spacing_bps: float
    invalidation: float
    take_profit: float
    entries: tuple[GridEntryLevel, ...]


def _campaign_id(signal: Signal, state: SymbolState) -> str:
    event_ms = state.last_event_time_ms
    if event_ms is None:
        raise GridPlanError("market event timestamp is required")
    raw = f"{signal.symbol.upper()}|{signal.direction.value}|{signal.setup}|{event_ms}"
    return blake2s(raw.encode("utf-8"), digest_size=8).hexdigest()


def _reference_price(state: SymbolState) -> float:
    if state.best_bid and state.best_ask and state.best_bid > 0 and state.best_ask >= state.best_bid:
        return (state.best_bid + state.best_ask) / 2.0
    if state.bars and state.bars[-1].close > 0:
        return state.bars[-1].close
    raise GridPlanError("reference price unavailable")


def build_grid_plan(
    signal: Signal,
    decision: RiskDecision,
    state: SymbolState,
    config: GridConfig | None = None,
) -> GridPlan:
    cfg = config or GridConfig()
    if not decision.approved:
        raise GridPlanError(f"risk decision not approved: {decision.reason}")
    if signal.direction not in (Direction.LONG, Direction.SHORT):
        raise GridPlanError("grid requires LONG or SHORT signal")
    if signal.invalidation is None or signal.invalidation <= 0:
        raise GridPlanError("signal invalidation is required")
    if decision.notional_usdt <= 0 or decision.leverage < 1:
        raise GridPlanError("invalid risk allocation")

    volatility = natr(list(state.bars))
    if volatility is None or volatility <= 0:
        raise GridPlanError("NATR warmup is incomplete")
    reference = _reference_price(state)
    side = 1 if signal.direction is Direction.LONG else -1
    if side > 0 and signal.invalidation >= reference:
        raise GridPlanError("LONG invalidation must be below reference price")
    if side < 0 and signal.invalidation <= reference:
        raise GridPlanError("SHORT invalidation must be above reference price")

    spacing_bps = min(
        cfg.max_spacing_bps,
        max(cfg.min_spacing_bps, volatility * 10_000.0 * cfg.spacing_natr_multiplier),
    )
    spacing = spacing_bps / 10_000.0
    total = float(decision.notional_usdt)
    starter = total * cfg.starter_fraction
    remaining = total - starter
    per_limit = remaining / (cfg.entry_levels - 1)
    entries = [GridEntryLevel(0, "MARKET", reference, starter)]
    for index in range(1, cfg.entry_levels):
        price = reference * (1.0 - side * spacing * index)
        if price <= 0:
            raise GridPlanError("grid level price became non-positive")
        entries.append(GridEntryLevel(index, "LIMIT", price, per_limit))

    take_profit = reference * (1.0 + side * spacing * cfg.take_profit_steps)
    return GridPlan(
        campaign_id=_campaign_id(signal, state),
        symbol=signal.symbol.upper(),
        direction=signal.direction,
        reference_price=reference,
        total_notional_usdt=total,
        leverage=decision.leverage,
        spacing_bps=spacing_bps,
        invalidation=float(signal.invalidation),
        take_profit=take_profit,
        entries=tuple(entries),
    )
