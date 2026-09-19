from __future__ import annotations

from dataclasses import dataclass, field

from signalgrid.execution.grid import GridPlan
from signalgrid.market.state import SymbolState
from signalgrid.models import Direction, GridMode
from signalgrid.risk.engine import PositionView


class PaperExecutionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PaperFill:
    level_index: int
    price: float
    notional_usdt: float
    quantity: float
    direction: Direction


@dataclass(slots=True)
class PaperCampaign:
    plan: GridPlan
    status: str = "OPEN"
    fills: list[PaperFill] = field(default_factory=list)
    realized_pnl: float = 0.0
    close_reason: str | None = None
    close_price: float | None = None
    active_direction: Direction | None = None

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
    """Small bounded paper broker for the same grid plan used by authenticated execution."""

    def __init__(self, suppress_same_event_reopen: bool = True) -> None:
        self._campaigns: dict[str, PaperCampaign] = {}
        self.closed: list[PaperCampaign] = []
        self.suppress_same_event_reopen = suppress_same_event_reopen
        self._last_event_ms: dict[str, int | None] = {}
        self._recently_closed: dict[str, tuple[PaperCampaign, int | None]] = {}

    def open_campaign(self, plan: GridPlan) -> PaperCampaign:
        symbol = plan.symbol.upper()
        if self.active_campaign(symbol) is not None:
            raise PaperExecutionError(f"campaign already active or closed on same event for {symbol}")
        campaign = PaperCampaign(plan=plan)
        if plan.mode is GridMode.NEUTRAL_GRID:
            if not plan.entries or any(level.kind != "LIMIT" for level in plan.entries):
                raise PaperExecutionError("neutral grid must contain only LIMIT entries")
        else:
            starter = plan.entries[0]
            if starter.kind != "MARKET":
                raise PaperExecutionError("first directional grid level must be MARKET")
            campaign.active_direction = plan.direction
            self._fill(campaign, starter.index, starter.price, starter.notional_usdt, plan.direction)
        self._campaigns[symbol] = campaign
        return campaign

    def on_state(self, state: SymbolState) -> PaperCampaign | None:
        symbol = state.symbol.upper()
        event_ms = state.last_event_time_ms
        self._last_event_ms[symbol] = event_ms
        recent = self._recently_closed.get(symbol)
        if recent is not None and recent[1] != event_ms:
            self._recently_closed.pop(symbol, None)

        campaign = self._campaigns.get(symbol)
        if campaign is None:
            return None
        plan = campaign.plan
        bid = state.best_bid
        ask = state.best_ask
        if not bid or not ask or bid <= 0 or ask < bid:
            return campaign

        if plan.mode is GridMode.NEUTRAL_GRID:
            if campaign.active_direction is None:
                if plan.expires_at_ms and event_ms is not None and event_ms >= plan.expires_at_ms:
                    return self._close(campaign, (bid + ask) / 2.0, "EXPIRED", event_ms)
                self._fill_neutral_first_touch(campaign, bid, ask)
                if campaign.active_direction is None:
                    return campaign
            return self._manage_neutral_position(campaign, bid, ask, event_ms)

        if plan.direction is Direction.LONG:
            if bid <= plan.invalidation:
                return self._close(campaign, bid, "STOP", event_ms)
            if bid >= plan.take_profit:
                return self._close(campaign, bid, "TAKE_PROFIT", event_ms)
        else:
            if ask >= plan.invalidation:
                return self._close(campaign, ask, "STOP", event_ms)
            if ask <= plan.take_profit:
                return self._close(campaign, ask, "TAKE_PROFIT", event_ms)

        if plan.expires_at_ms and event_ms is not None and event_ms >= plan.expires_at_ms:
            expiry_price = bid if plan.direction is Direction.LONG else ask
            return self._close(campaign, expiry_price, "MAX_HOLD", event_ms)

        filled = campaign.filled_indices
        for level in plan.entries[1:]:
            if level.index in filled:
                continue
            should_fill = ask <= level.price if plan.direction is Direction.LONG else bid >= level.price
            if should_fill:
                self._fill(campaign, level.index, level.price, level.notional_usdt, plan.direction)
        return campaign

    def _fill_neutral_first_touch(self, campaign: PaperCampaign, bid: float, ask: float) -> None:
        touched = []
        for level in campaign.plan.entries:
            if level.direction is Direction.LONG and ask <= level.price:
                touched.append(level)
            elif level.direction is Direction.SHORT and bid >= level.price:
                touched.append(level)
        if not touched:
            return
        # A single event spanning both sides is ambiguous for a bar/tick-level
        # paper model. Refuse to invent sequencing that did not exist.
        directions = {level.direction for level in touched}
        if len(directions) != 1:
            raise PaperExecutionError("ambiguous neutral-grid two-sided fill in one market event")
        direction = touched[0].direction
        campaign.active_direction = direction
        for level in sorted(touched, key=lambda x: x.index):
            if level.direction is direction:
                self._fill(campaign, level.index, level.price, level.notional_usdt, direction)

    def _manage_neutral_position(
        self,
        campaign: PaperCampaign,
        bid: float,
        ask: float,
        event_ms: int | None,
    ) -> PaperCampaign:
        plan = campaign.plan
        direction = campaign.active_direction
        assert direction in (Direction.LONG, Direction.SHORT)

        if direction is Direction.LONG:
            stop = plan.neutral_long_invalidation
            take_profit = plan.neutral_long_take_profit
            if stop is None or take_profit is None:
                raise PaperExecutionError("neutral LONG protection is incomplete")
            if bid <= stop:
                return self._close(campaign, bid, "STOP", event_ms)
            if bid >= take_profit:
                return self._close(campaign, bid, "TAKE_PROFIT", event_ms)
        else:
            stop = plan.neutral_short_invalidation
            take_profit = plan.neutral_short_take_profit
            if stop is None or take_profit is None:
                raise PaperExecutionError("neutral SHORT protection is incomplete")
            if ask >= stop:
                return self._close(campaign, ask, "STOP", event_ms)
            if ask <= take_profit:
                return self._close(campaign, ask, "TAKE_PROFIT", event_ms)

        filled = campaign.filled_indices
        for level in plan.entries:
            if level.index in filled or level.direction is not direction:
                continue
            should_fill = ask <= level.price if direction is Direction.LONG else bid >= level.price
            if should_fill:
                self._fill(campaign, level.index, level.price, level.notional_usdt, direction)
        return campaign

    def position_views(self) -> list[PositionView]:
        out: list[PositionView] = []
        for campaign in self._campaigns.values():
            if campaign.status != "OPEN" or campaign.notional_usdt <= 0:
                continue
            direction = campaign.active_direction or campaign.plan.direction
            if direction in (Direction.LONG, Direction.SHORT):
                out.append(PositionView(campaign.plan.symbol, direction, campaign.notional_usdt))
        return out

    def active_campaign(self, symbol: str) -> PaperCampaign | None:
        symbol = symbol.upper()
        active = self._campaigns.get(symbol)
        if active is not None:
            return active
        if not self.suppress_same_event_reopen:
            return None
        recent = self._recently_closed.get(symbol)
        if recent is None:
            return None
        campaign, closed_event_ms = recent
        return campaign if closed_event_ms == self._last_event_ms.get(symbol) else None

    def _fill(
        self,
        campaign: PaperCampaign,
        index: int,
        price: float,
        notional: float,
        direction: Direction,
    ) -> None:
        if notional <= 0 or price <= 0:
            raise PaperExecutionError("invalid paper fill")
        if direction not in (Direction.LONG, Direction.SHORT):
            raise PaperExecutionError("paper fill requires LONG or SHORT direction")
        campaign.fills.append(PaperFill(index, price, notional, notional / price, direction))

    def _close(self, campaign: PaperCampaign, price: float, reason: str, event_ms: int | None) -> PaperCampaign:
        direction = campaign.active_direction or campaign.plan.direction
        if campaign.quantity > 0 and direction in (Direction.LONG, Direction.SHORT):
            side = 1 if direction is Direction.LONG else -1
            campaign.realized_pnl = campaign.quantity * (price - campaign.average_entry) * side
        else:
            campaign.realized_pnl = 0.0
        campaign.status = "CLOSED"
        campaign.close_reason = reason
        campaign.close_price = price
        symbol = campaign.plan.symbol.upper()
        self._campaigns.pop(symbol, None)
        if self.suppress_same_event_reopen:
            self._recently_closed[symbol] = (campaign, event_ms)
        self.closed.append(campaign)
        return campaign
