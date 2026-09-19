from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from time import time
from typing import Any

from signalgrid.execution.binance import (
    BinanceExecutionPort,
    BinanceOrderNotFoundError,
    LimitEntryIntent,
    OrderIntent,
    ProtectiveExitIntent,
    ReduceOnlyMarketIntent,
    client_order_id,
)
from signalgrid.execution.grid import GridPlan
from signalgrid.models import Direction, GridMode
from signalgrid.state.store import ACTIVE_ALGO_STATUSES, ACTIVE_ORDER_STATUSES, StateStore


class GridCampaignError(RuntimeError):
    pass


class GridCampaignStateError(GridCampaignError):
    pass


ACTIVE_CAMPAIGN_STATUSES = {"OPENING", "ACTIVE", "DEGRADED"}


@dataclass(frozen=True, slots=True)
class GridCampaignRecord:
    campaign_id: str
    symbol: str
    direction: str
    status: str
    reference_price: float
    total_notional_usdt: float
    leverage: int
    spacing_bps: float
    invalidation: float
    take_profit: float
    starter_client_id: str
    limit_client_ids: tuple[str, ...]
    stop_client_id: str
    take_profit_client_id: str
    created_at_ms: int
    updated_at_ms: int
    mode: str = ""
    activated: bool = True
    expires_at_ms: int = 0
    neutral_long_invalidation: float | None = None
    neutral_long_take_profit: float | None = None
    neutral_short_invalidation: float | None = None
    neutral_short_take_profit: float | None = None


@dataclass(frozen=True, slots=True)
class GridOpenResult:
    campaign: GridCampaignRecord
    starter_exchange_order_id: str
    limit_exchange_order_ids: tuple[str, ...]


class GridCampaignRegistry:
    """Small persisted ownership registry backed by StateStore runtime_state."""

    RUNTIME_KEY = "grid_campaigns_v1"

    def __init__(self, store: StateStore):
        self.store = store

    def _load_raw(self) -> dict[str, dict[str, Any]]:
        raw = self.store.get_runtime(self.RUNTIME_KEY, "{}") or "{}"
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            self.store.halt("GRID_CAMPAIGN_REGISTRY_CORRUPT")
            raise GridCampaignStateError("grid campaign registry is corrupt") from exc
        if not isinstance(value, dict):
            self.store.halt("GRID_CAMPAIGN_REGISTRY_INVALID")
            raise GridCampaignStateError("grid campaign registry must be an object")
        return value

    def _save_raw(self, value: dict[str, dict[str, Any]]) -> None:
        self.store.set_runtime(self.RUNTIME_KEY, json.dumps(value, sort_keys=True, separators=(",", ":")))

    @staticmethod
    def _from_dict(raw: dict[str, Any]) -> GridCampaignRecord:
        return GridCampaignRecord(
            campaign_id=str(raw["campaign_id"]),
            symbol=str(raw["symbol"]).upper(),
            direction=str(raw["direction"]),
            status=str(raw["status"]),
            reference_price=float(raw["reference_price"]),
            total_notional_usdt=float(raw["total_notional_usdt"]),
            leverage=int(raw["leverage"]),
            spacing_bps=float(raw["spacing_bps"]),
            invalidation=float(raw["invalidation"]),
            take_profit=float(raw["take_profit"]),
            starter_client_id=str(raw["starter_client_id"]),
            limit_client_ids=tuple(str(x) for x in raw.get("limit_client_ids", [])),
            stop_client_id=str(raw["stop_client_id"]),
            take_profit_client_id=str(raw["take_profit_client_id"]),
            created_at_ms=int(raw["created_at_ms"]),
            updated_at_ms=int(raw["updated_at_ms"]),
            mode=str(raw.get("mode") or (
                GridMode.LONG_GRID.value if str(raw.get("direction")) == Direction.LONG.value
                else GridMode.SHORT_GRID.value if str(raw.get("direction")) == Direction.SHORT.value
                else GridMode.NEUTRAL_GRID.value
            )),
            activated=bool(raw.get("activated", True)),
            expires_at_ms=int(raw.get("expires_at_ms", 0)),
            neutral_long_invalidation=None if raw.get("neutral_long_invalidation") is None else float(raw["neutral_long_invalidation"]),
            neutral_long_take_profit=None if raw.get("neutral_long_take_profit") is None else float(raw["neutral_long_take_profit"]),
            neutral_short_invalidation=None if raw.get("neutral_short_invalidation") is None else float(raw["neutral_short_invalidation"]),
            neutral_short_take_profit=None if raw.get("neutral_short_take_profit") is None else float(raw["neutral_short_take_profit"]),
        )

    def records(self) -> tuple[GridCampaignRecord, ...]:
        return tuple(self._from_dict(v) for _, v in sorted(self._load_raw().items()))

    def get(self, symbol: str) -> GridCampaignRecord | None:
        raw = self._load_raw().get(symbol.upper())
        return self._from_dict(raw) if raw else None

    def active(self, symbol: str) -> GridCampaignRecord | None:
        record = self.get(symbol)
        return record if record and record.status in ACTIVE_CAMPAIGN_STATUSES else None

    def prepare(self, plan: GridPlan) -> GridCampaignRecord:
        symbol = plan.symbol.upper()
        if self.active(symbol) is not None:
            raise GridCampaignStateError(f"active campaign already exists for {symbol}")
        now_ms = int(time() * 1000)
        neutral = plan.mode is GridMode.NEUTRAL_GRID
        record = GridCampaignRecord(
            campaign_id=plan.campaign_id,
            symbol=symbol,
            direction="NEUTRAL" if neutral else plan.direction.value,
            status="OPENING",
            reference_price=plan.reference_price,
            total_notional_usdt=plan.total_notional_usdt,
            leverage=plan.leverage,
            spacing_bps=plan.spacing_bps,
            invalidation=plan.invalidation,
            take_profit=plan.take_profit,
            starter_client_id="" if neutral else client_order_id("e", f"{plan.campaign_id}:starter"),
            limit_client_ids=tuple(
                client_order_id("g", f"{plan.campaign_id}:level:{level.index}")
                for level in plan.entries
                if level.kind == "LIMIT"
            ),
            stop_client_id=client_order_id("x", f"{plan.campaign_id}:stop"),
            take_profit_client_id=client_order_id("x", f"{plan.campaign_id}:tp"),
            created_at_ms=now_ms,
            updated_at_ms=now_ms,
            mode=plan.mode.value,
            activated=not neutral,
            expires_at_ms=plan.expires_at_ms,
            neutral_long_invalidation=plan.neutral_long_invalidation,
            neutral_long_take_profit=plan.neutral_long_take_profit,
            neutral_short_invalidation=plan.neutral_short_invalidation,
            neutral_short_take_profit=plan.neutral_short_take_profit,
        )
        raw = self._load_raw()
        raw[symbol] = asdict(record)
        self._save_raw(raw)
        return record

    def set_status(self, symbol: str, status: str) -> GridCampaignRecord:
        record = self.get(symbol)
        if record is None:
            raise GridCampaignStateError(f"campaign not found for {symbol.upper()}")
        updated = replace(record, status=status, updated_at_ms=int(time() * 1000))
        raw = self._load_raw()
        raw[record.symbol] = asdict(updated)
        self._save_raw(raw)
        return updated

    def activate_neutral(self, symbol: str, direction: Direction) -> GridCampaignRecord:
        record = self.get(symbol)
        if record is None:
            raise GridCampaignStateError(f"campaign not found for {symbol.upper()}")
        if record.mode != GridMode.NEUTRAL_GRID.value:
            raise GridCampaignStateError(f"campaign is not neutral for {symbol.upper()}")
        updated = replace(
            record,
            direction=direction.value,
            activated=True,
            status="ACTIVE",
            updated_at_ms=int(time() * 1000),
        )
        raw = self._load_raw()
        raw[record.symbol] = asdict(updated)
        self._save_raw(raw)
        return updated

    def mark_closed(self, symbol: str) -> GridCampaignRecord:
        return self.set_status(symbol, "CLOSED")


class GridCampaignExecutor:
    """Open/recover/clean one bounded grid campaign without strategy logic."""

    def __init__(self, adapter: BinanceExecutionPort, store: StateStore, registry: GridCampaignRegistry | None = None):
        self.adapter = adapter
        self.store = store
        self.registry = registry or GridCampaignRegistry(store)

    async def open_campaign(self, plan: GridPlan) -> GridOpenResult:
        if plan.mode is GridMode.NEUTRAL_GRID:
            return await self._open_neutral(plan)
        return await self._open_directional(plan)

    async def _open_directional(self, plan: GridPlan) -> GridOpenResult:
        record = self.registry.prepare(plan)
        starter_receipt = None
        stop_placed = False
        limit_order_ids: list[str] = []
        try:
            starter = plan.entries[0]
            stop_intent = ProtectiveExitIntent(
                plan.symbol,
                plan.direction,
                plan.invalidation,
                f"{plan.campaign_id}:stop",
                order_type="STOP_MARKET",
            )
            take_profit_intent = ProtectiveExitIntent(
                plan.symbol,
                plan.direction,
                plan.take_profit,
                f"{plan.campaign_id}:tp",
                order_type="TAKE_PROFIT_MARKET",
            )
            self.adapter.validate_protective_exit(stop_intent)
            self.adapter.validate_protective_exit(take_profit_intent)
            starter_receipt = await self.adapter.place_entry_receipt(
                OrderIntent(
                    plan.symbol,
                    plan.direction,
                    starter.notional_usdt,
                    plan.leverage,
                    plan.invalidation,
                    f"{plan.campaign_id}:starter",
                    plan.reference_price,
                )
            )
            await self.adapter.place_protective_exit(stop_intent)
            stop_placed = True
            await self.adapter.place_protective_exit(take_profit_intent)
            for level in plan.entries[1:]:
                receipt = await self.adapter.place_limit_entry_receipt(
                    LimitEntryIntent(
                        plan.symbol,
                        plan.direction,
                        level.notional_usdt,
                        level.price,
                        f"{plan.campaign_id}:level:{level.index}",
                    )
                )
                limit_order_ids.append(receipt.exchange_order_id)
            record = self.registry.set_status(plan.symbol, "ACTIVE")
            return GridOpenResult(record, starter_receipt.exchange_order_id, tuple(limit_order_ids))
        except Exception as exc:
            if starter_receipt is None:
                self.registry.set_status(plan.symbol, "FAILED")
                raise GridCampaignError("grid starter entry failed before exposure") from exc
            if not stop_placed:
                try:
                    await self.adapter.place_reduce_only_market(
                        ReduceOnlyMarketIntent(
                            plan.symbol,
                            plan.direction,
                            starter_receipt.quantity,
                            f"{plan.campaign_id}:emergency-close",
                        )
                    )
                except Exception:
                    self.store.halt(f"GRID_UNPROTECTED_POSITION:{plan.symbol}")
                    self.registry.set_status(plan.symbol, "DEGRADED")
                    raise GridCampaignError("grid open failed and emergency close also failed") from exc
                self.registry.set_status(plan.symbol, "FAILED")
                self.store.halt(f"GRID_STOP_PLACEMENT_FAILED:{plan.symbol}")
                raise GridCampaignError("grid open failed before protective stop; starter was emergency-closed") from exc
            self.registry.set_status(plan.symbol, "DEGRADED")
            self.store.halt(f"GRID_OPEN_DEGRADED:{plan.symbol}")
            raise GridCampaignError("grid open degraded after protective stop; new entries halted") from exc

    async def _open_neutral(self, plan: GridPlan) -> GridOpenResult:
        record = self.registry.prepare(plan)
        placed: list[tuple[str, str]] = []
        try:
            for level in plan.entries:
                if level.kind != "LIMIT" or level.direction not in (Direction.LONG, Direction.SHORT):
                    raise GridCampaignError("neutral grid contains invalid entry")
                receipt = await self.adapter.place_limit_entry_receipt(
                    LimitEntryIntent(
                        plan.symbol,
                        level.direction,
                        level.notional_usdt,
                        level.price,
                        f"{plan.campaign_id}:level:{level.index}",
                    )
                )
                placed.append((receipt.client_order_id, receipt.exchange_order_id))
            record = self.registry.set_status(plan.symbol, "ACTIVE")
            position = self.store.get_account_position(plan.symbol)
            if position is not None and position.quantity != 0:
                record = await self._activate_neutral_position(record)
            return GridOpenResult(record, "", tuple(order_id for _, order_id in placed))
        except Exception as exc:
            position = self.store.get_account_position(plan.symbol)
            if position is None or position.quantity == 0:
                for _, order_id in placed:
                    if not order_id:
                        continue
                    try:
                        await self.adapter.cancel_order(plan.symbol, order_id)
                    except BinanceOrderNotFoundError:
                        pass
                    except Exception:
                        self.store.halt(f"NEUTRAL_GRID_PARTIAL_OPEN_CLEANUP_FAILED:{plan.symbol}")
                        self.registry.set_status(plan.symbol, "DEGRADED")
                        raise GridCampaignError("neutral grid partial-open cleanup failed") from exc
                self.registry.set_status(plan.symbol, "FAILED")
                raise GridCampaignError("neutral grid open failed before exposure") from exc

            direction = Direction.LONG if position.direction == Direction.LONG.value else Direction.SHORT
            try:
                await self.adapter.place_reduce_only_market(
                    ReduceOnlyMarketIntent(
                        plan.symbol,
                        direction,
                        position.quantity,
                        f"{plan.campaign_id}:neutral-open-emergency-close",
                    )
                )
            except Exception:
                self.registry.set_status(plan.symbol, "DEGRADED")
                self.store.halt(f"NEUTRAL_GRID_UNPROTECTED_POSITION:{plan.symbol}")
                raise GridCampaignError("neutral grid open failed with live exposure and emergency close failed") from exc
            self.registry.set_status(plan.symbol, "FAILED")
            self.store.halt(f"NEUTRAL_GRID_PARTIAL_FILL_DURING_OPEN:{plan.symbol}")
            raise GridCampaignError("neutral grid partially filled while opening; exposure emergency-closed") from exc

    def _neutral_protection(self, record: GridCampaignRecord, direction: Direction) -> tuple[float, float]:
        if direction is Direction.LONG:
            stop = record.neutral_long_invalidation
            take_profit = record.neutral_long_take_profit
        else:
            stop = record.neutral_short_invalidation
            take_profit = record.neutral_short_take_profit
        if stop is None or take_profit is None or stop <= 0 or take_profit <= 0:
            raise GridCampaignStateError(f"neutral protection missing for {record.symbol}:{direction.value}")
        return stop, take_profit

    async def _cancel_neutral_opposite_entries(self, record: GridCampaignRecord, direction: Direction) -> None:
        keep_side = "BUY" if direction is Direction.LONG else "SELL"
        for cid in record.limit_client_ids:
            order = self.store.get_order(cid)
            if order is None or order.status not in ACTIVE_ORDER_STATUSES:
                continue
            if order.side == keep_side or not order.exchange_order_id:
                continue
            try:
                await self.adapter.cancel_order(record.symbol, order.exchange_order_id)
            except BinanceOrderNotFoundError:
                continue

    async def _emergency_close_position(
        self,
        record: GridCampaignRecord,
        direction: Direction,
        quantity,
        suffix: str,
    ) -> None:
        await self.adapter.place_reduce_only_market(
            ReduceOnlyMarketIntent(
                record.symbol,
                direction,
                quantity,
                f"{record.campaign_id}:{suffix}",
            )
        )

    async def _activate_neutral_position(self, record: GridCampaignRecord) -> GridCampaignRecord:
        position = self.store.get_account_position(record.symbol)
        if position is None or position.quantity == 0:
            return record
        direction = Direction.LONG if position.direction == Direction.LONG.value else Direction.SHORT

        if record.activated:
            if record.direction != direction.value:
                try:
                    await self._emergency_close_position(record, direction, position.quantity, "direction-flip-close")
                finally:
                    self.registry.set_status(record.symbol, "DEGRADED")
                    self.store.halt(f"NEUTRAL_GRID_DIRECTION_FLIP:{record.symbol}")
                raise GridCampaignError(f"neutral grid direction flipped unexpectedly for {record.symbol}")
            await self._cancel_neutral_opposite_entries(record, direction)
            return record

        stop_price, take_profit_price = self._neutral_protection(record, direction)
        stop_intent = ProtectiveExitIntent(
            record.symbol,
            direction,
            stop_price,
            f"{record.campaign_id}:stop",
            order_type="STOP_MARKET",
        )
        take_profit_intent = ProtectiveExitIntent(
            record.symbol,
            direction,
            take_profit_price,
            f"{record.campaign_id}:tp",
            order_type="TAKE_PROFIT_MARKET",
        )
        stop_placed = False
        try:
            self.adapter.validate_protective_exit(stop_intent)
            self.adapter.validate_protective_exit(take_profit_intent)
            await self.adapter.place_protective_exit(stop_intent)
            stop_placed = True
            await self.adapter.place_protective_exit(take_profit_intent)
            await self._cancel_neutral_opposite_entries(record, direction)
            return self.registry.activate_neutral(record.symbol, direction)
        except Exception as exc:
            if not stop_placed:
                try:
                    await self._emergency_close_position(
                        record,
                        direction,
                        position.quantity,
                        "neutral-protection-emergency-close",
                    )
                except Exception:
                    self.registry.set_status(record.symbol, "DEGRADED")
                    self.store.halt(f"NEUTRAL_GRID_UNPROTECTED_POSITION:{record.symbol}")
                    raise GridCampaignError("neutral position protection and emergency close both failed") from exc
                self.registry.set_status(record.symbol, "FAILED")
                self.store.halt(f"NEUTRAL_GRID_PROTECTION_FAILED:{record.symbol}")
                raise GridCampaignError("neutral position protection failed; exposure emergency-closed") from exc
            self.registry.set_status(record.symbol, "DEGRADED")
            self.store.halt(f"NEUTRAL_GRID_OPEN_DEGRADED:{record.symbol}")
            raise GridCampaignError("neutral position degraded after protective stop") from exc

    async def cleanup_if_flat(self, symbol: str) -> bool:
        record = self.registry.active(symbol)
        if record is None:
            return False
        position = self.store.get_account_position(record.symbol)

        if record.mode == GridMode.NEUTRAL_GRID.value:
            if position is not None and position.quantity != 0:
                await self._activate_neutral_position(record)
                return False
            if not record.activated:
                # Armed two-sided neutral campaign has no exposure and therefore
                # intentionally has no STOP/TP yet.
                return False

        if position is not None and position.quantity != 0:
            return False

        pending_exchange_convergence = False
        try:
            for cid in record.limit_client_ids:
                order = self.store.get_order(cid)
                if order is not None and order.status in ACTIVE_ORDER_STATUSES and order.exchange_order_id:
                    try:
                        await self.adapter.cancel_order(record.symbol, order.exchange_order_id)
                    except BinanceOrderNotFoundError:
                        pending_exchange_convergence = True
            for cid in (record.stop_client_id, record.take_profit_client_id):
                algo = self.store.get_algo_order(cid)
                if algo is not None and algo.status in ACTIVE_ALGO_STATUSES:
                    try:
                        await self.adapter.cancel_algo_order(cid)
                    except BinanceOrderNotFoundError:
                        pending_exchange_convergence = True
        except Exception as exc:
            self.registry.set_status(record.symbol, "DEGRADED")
            self.store.halt(f"GRID_CLEANUP_FAILED:{record.symbol}")
            raise GridCampaignError("grid cleanup failed") from exc

        reopened = self.store.get_account_position(record.symbol)
        if reopened is not None and reopened.quantity != 0:
            self.registry.set_status(record.symbol, "DEGRADED")
            self.store.halt(f"GRID_CLEANUP_RACE:{record.symbol}")
            raise GridCampaignError("position reopened while cleanup was running")
        if pending_exchange_convergence:
            return False
        self.registry.mark_closed(record.symbol)
        return True

    async def recover_after_reconciliation(self) -> None:
        for record in self.registry.records():
            if record.status not in ACTIVE_CAMPAIGN_STATUSES:
                continue
            position = self.store.get_account_position(record.symbol)

            if record.mode == GridMode.NEUTRAL_GRID.value:
                if position is None or position.quantity == 0:
                    if record.activated:
                        await self.cleanup_if_flat(record.symbol)
                    else:
                        self.registry.set_status(record.symbol, "ACTIVE")
                    continue
                if not record.activated:
                    record = await self._activate_neutral_position(record)
                else:
                    direction = Direction.LONG if position.direction == Direction.LONG.value else Direction.SHORT
                    if record.direction != direction.value:
                        self.registry.set_status(record.symbol, "DEGRADED")
                        self.store.halt(f"NEUTRAL_GRID_DIRECTION_FLIP:{record.symbol}")
                        raise GridCampaignError(f"neutral grid direction mismatch for {record.symbol}")
                    await self._cancel_neutral_opposite_entries(record, direction)

            elif position is None or position.quantity == 0:
                await self.cleanup_if_flat(record.symbol)
                continue

            stop = self.store.get_algo_order(record.stop_client_id)
            tp = self.store.get_algo_order(record.take_profit_client_id)
            if stop is None or stop.status not in ACTIVE_ALGO_STATUSES:
                self.registry.set_status(record.symbol, "DEGRADED")
                self.store.halt(f"GRID_RECOVERY_MISSING_STOP:{record.symbol}")
                raise GridCampaignError(f"active position lacks active stop for {record.symbol}")
            if tp is None or tp.status not in ACTIVE_ALGO_STATUSES:
                self.registry.set_status(record.symbol, "DEGRADED")
                self.store.halt(f"GRID_RECOVERY_MISSING_TP:{record.symbol}")
                raise GridCampaignError(f"active position lacks active take-profit for {record.symbol}")
            self.registry.set_status(record.symbol, "ACTIVE")

