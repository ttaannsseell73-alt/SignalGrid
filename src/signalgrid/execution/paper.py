from __future__ import annotations

from dataclasses import dataclass, field

from signalgrid.execution.grid import GridPlan
from signalgrid.market.state import SymbolState
from signalgrid.models import Direction
from signalgrid.risk.engine import PositionView


class PaperExecutionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PaperFill:
    level_index: int
    price: float
    notional_usdt: float
    quantity: float


@dataclass(slots=True)
class PaperCampaign:
    plan: GridPlan
    status: str = "OPEN"
    fills: list[PaperFill] = field(default_factory=list)
    realized_pnl: float = 0.0
    close_reason: str | None = None
    close_price: float | None = None

    @property
    def filled_indices(self) -> set[int]:
        return {fill.level_index for fill in self.fills}

    @property
    def quantity(self) -> float:
        return sum(fill.quantity for fill in self.fills)

    @property
    def notional_usdt(self) -> float:
        return sum(fill.notional_usdt for fill in self.fills)

    @property
    def average_entry(self) -> float:
        qty = self.quantity
        if qty <= 0:
            return 0.0
        return sum(fill.quantity * fill.price for fill in self.fills) / qty


class PaperGridBroker:
    """Small bounded paper broker for the same grid plan used by live execution."""

    def __init__(self) -> None:
        self._campaigns: dict[str, PaperCampaign] = {}
        self.closed: list[PaperCampaign] = []

    def open_campaign(self, plan: GridPlan) -> PaperCampaign:
        symbol = plan.symbol.upper()
        if symbol in self._campaigns:
            raise PaperExecutionError(f"campaign already active for {symbol}")
        starter = plan.entries[0]
        if starter.kind != "MARKET":
            raise PaperExecutionError("first grid level must be MARKET")
        campaign = PaperCampaign(plan=plan)
        self._fill(campaign, starter.index, starter.price, starter.notional_usdt)
        self._campaigns[symbol] = campaign
        return campaign

    def on_state(self, state: SymbolState) -> PaperCampaign | None:
        symbol = state.symbol.upper()
        campaign = self._campaigns.get(symbol)
        if campaign is None:
            return None
        plan = campaign.plan
        bid = state.best_bid
        ask = state.best_ask
        if not bid or not ask or bid <= 0 or ask < bid:
            return campaign

        if plan.direction is Direction.LONG:
            if bid <= plan.invalidation:
                return self._close(campaign, bid, "STOP")
            if bid >= plan.take_profit:
                return self._close(campaign, bid, "TAKE_PROFIT")
        else:
            if ask >= plan.invalidation:
                return self._close(campaign, ask, "STOP")
            if ask <= plan.take_profit:
                return self._close(campaign, ask, "TAKE_PROFIT")

        filled = campaign.filled_indices
        for level in plan.entries[1:]:
            if level.index in filled:
                continue
            should_fill = ask <= level.price if plan.direction is Direction.LONG else bid >= level.price
            if should_fill:
                self._fill(campaign, level.index, level.price, level.notional_usdt)
        return campaign

    def position_views(self) -> list[PositionView]:
        return [
            PositionView(c.plan.symbol, c.plan.direction, c.notional_usdt)
            for c in self._campaigns.values()
            if c.status == "OPEN" and c.notional_usdt > 0
        ]

    def active_campaign(self, symbol: str) -> PaperCampaign | None:
        return self._campaigns.get(symbol.upper())

    def _fill(self, campaign: PaperCampaign, index: int, price: float, notional: float) -> None:
        if notional <= 0 or price <= 0:
            raise PaperExecutionError("invalid paper fill")
        campaign.fills.append(PaperFill(index, price, notional, notional / price))

    def _close(self, campaign: PaperCampaign, price: float, reason: str) -> PaperCampaign:
        side = 1 if campaign.plan.direction is Direction.LONG else -1
        campaign.realized_pnl = campaign.quantity * (price - campaign.average_entry) * side
        campaign.status = "CLOSED"
        campaign.close_reason = reason
        campaign.close_price = price
        self._campaigns.pop(campaign.plan.symbol.upper(), None)
        self.closed.append(campaign)
        return campaign
