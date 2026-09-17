from __future__ import annotations
from dataclasses import dataclass
from signalgrid.models import Direction

@dataclass(slots=True)
class OrderIntent:
    symbol: str
    direction: Direction
    notional_usdt: float
    leverage: int
    invalidation: float | None

class BinanceExecutionPort:
    """Small boundary around Binance. No strategy logic is allowed here."""

    async def place_entry(self, intent: OrderIntent) -> str:
        raise NotImplementedError("Binance authenticated adapter is the next milestone")

    async def cancel_order(self, symbol: str, order_id: str) -> None:
        raise NotImplementedError
