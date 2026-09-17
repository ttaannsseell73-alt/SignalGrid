from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from time import time
from typing import Any

from signalgrid.state.store import StateStore, StoredAccountPosition, StoredAlgoOrder, StoredOrder


class ReconciliationError(RuntimeError):
    pass


class ReconciliationUnavailableError(ReconciliationError):
    pass


class StateMismatchError(ReconciliationError):
    pass


@dataclass(frozen=True, slots=True)
class ExchangeOrderSnapshot:
    client_order_id: str
    exchange_order_id: str
    symbol: str
    side: str
    status: str
    order_type: str
    orig_qty: Decimal
    filled_qty: Decimal
    avg_price: Decimal
    reduce_only: bool


@dataclass(frozen=True, slots=True)
class ExchangeAlgoSnapshot:
    client_algo_id: str
    algo_id: str
    symbol: str
    side: str
    status: str
    algo_type: str
    order_type: str
    trigger_price: Decimal
    quantity: Decimal
    close_position: bool
    reduce_only: bool
    actual_order_id: str


@dataclass(frozen=True, slots=True)
class ExchangePositionSnapshot:
    symbol: str
    direction: str
    quantity: Decimal
    entry_price: Decimal


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    orders: tuple[ExchangeOrderSnapshot, ...]
    algo_orders: tuple[ExchangeAlgoSnapshot, ...]
    positions: tuple[ExchangePositionSnapshot, ...]


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    ok: bool
    order_count: int
    algo_order_count: int
    position_count: int
    mismatches: tuple[str, ...] = ()


def _model_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(by_alias=True, exclude_none=True)
    as_dict = getattr(value, "dict", None)
    if callable(as_dict):
        return as_dict(by_alias=True, exclude_none=True)
    return vars(value)


def _unwrap(response: Any) -> Any:
    data = getattr(response, "data", None)
    return data() if callable(data) else response


def _as_list(response: Any) -> list[Any]:
    value = _unwrap(response)
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, dict):
        for key in ("orders", "positions", "data", "rows"):
            candidate = value.get(key)
            if isinstance(candidate, list):
                return candidate
        return [value]
    root = getattr(value, "root", None)
    if isinstance(root, list):
        return root
    return list(value) if hasattr(value, "__iter__") and not isinstance(value, (str, bytes)) else [value]


def _pick(raw: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in raw and raw[name] is not None:
            return raw[name]
    return default


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes"}


def parse_open_orders(response: Any) -> tuple[ExchangeOrderSnapshot, ...]:
    out: list[ExchangeOrderSnapshot] = []
    for item in _as_list(response):
        raw = _model_dict(item)
        client_id = str(_pick(raw, "clientOrderId", "client_order_id", default=""))
        order_id = str(_pick(raw, "orderId", "order_id", default=""))
        if not client_id and not order_id:
            continue
        out.append(
            ExchangeOrderSnapshot(
                client_order_id=client_id,
                exchange_order_id=order_id,
                symbol=str(_pick(raw, "symbol", default="")).upper(),
                side=str(_pick(raw, "side", default="")).upper(),
                status=str(_pick(raw, "status", default="")).upper(),
                order_type=str(_pick(raw, "type", "orderType", "order_type", default="")).upper(),
                orig_qty=Decimal(str(_pick(raw, "origQty", "orig_qty", default="0"))),
                filled_qty=Decimal(str(_pick(raw, "executedQty", "executed_qty", default="0"))),
                avg_price=Decimal(str(_pick(raw, "avgPrice", "avg_price", default="0"))),
                reduce_only=_bool(_pick(raw, "reduceOnly", "reduce_only", default=False)),
            )
        )
    return tuple(sorted(out, key=lambda x: (x.symbol, x.client_order_id, x.exchange_order_id)))


def parse_open_algo_orders(response: Any) -> tuple[ExchangeAlgoSnapshot, ...]:
    out: list[ExchangeAlgoSnapshot] = []
    for item in _as_list(response):
        raw = _model_dict(item)
        client_id = str(_pick(raw, "clientAlgoId", "client_algo_id", default=""))
        algo_id = str(_pick(raw, "algoId", "algo_id", default=""))
        if not client_id and not algo_id:
            continue
        out.append(
            ExchangeAlgoSnapshot(
                client_algo_id=client_id,
                algo_id=algo_id,
                symbol=str(_pick(raw, "symbol", default="")).upper(),
                side=str(_pick(raw, "side", default="")).upper(),
                status=str(_pick(raw, "algoStatus", "algo_status", default="")).upper(),
                algo_type=str(_pick(raw, "algoType", "algo_type", default="")).upper(),
                order_type=str(_pick(raw, "orderType", "order_type", default="")).upper(),
                trigger_price=Decimal(str(_pick(raw, "triggerPrice", "trigger_price", default="0"))),
                quantity=Decimal(str(_pick(raw, "quantity", default="0"))),
                close_position=_bool(_pick(raw, "closePosition", "close_position", default=False)),
                reduce_only=_bool(_pick(raw, "reduceOnly", "reduce_only", default=False)),
                actual_order_id=str(_pick(raw, "actualOrderId", "actual_order_id", default="")),
            )
        )
    return tuple(sorted(out, key=lambda x: (x.symbol, x.client_algo_id, x.algo_id)))


def parse_positions(response: Any) -> tuple[ExchangePositionSnapshot, ...]:
    out: list[ExchangePositionSnapshot] = []
    seen: set[str] = set()
    for item in _as_list(response):
        raw = _model_dict(item)
        symbol = str(_pick(raw, "symbol", default="")).upper()
        if not symbol:
            continue
        qty_signed = Decimal(str(_pick(raw, "positionAmt", "position_amt", default="0")))
        if qty_signed == 0:
            continue
        position_side = str(_pick(raw, "positionSide", "position_side", default="BOTH")).upper()
        if position_side != "BOTH":
            raise StateMismatchError(f"HEDGE_MODE_UNSUPPORTED:{symbol}:{position_side}")
        if symbol in seen:
            raise StateMismatchError(f"DUPLICATE_POSITION_SYMBOL:{symbol}")
        seen.add(symbol)
        direction = "LONG" if qty_signed > 0 else "SHORT"
        out.append(
            ExchangePositionSnapshot(
                symbol=symbol,
                direction=direction,
                quantity=abs(qty_signed),
                entry_price=Decimal(str(_pick(raw, "entryPrice", "entry_price", default="0"))),
            )
        )
    return tuple(sorted(out, key=lambda x: x.symbol))


class BinanceRestSnapshotProvider:
    """REST is used only to reconcile authoritative account state at startup/reconnect."""

    def __init__(self, rest_api: Any) -> None:
        self.rest_api = rest_api

    def snapshot(self) -> AccountSnapshot:
        try:
            open_orders = self.rest_api.current_all_open_orders()
            open_algos = self.rest_api.current_all_algo_open_orders()
            positions = self.rest_api.position_information_v3()
            parsed_positions = parse_positions(positions)
        except ReconciliationError:
            raise
        except Exception as exc:
            raise ReconciliationUnavailableError(str(exc)) from exc
        return AccountSnapshot(
            parse_open_orders(open_orders),
            parse_open_algo_orders(open_algos),
            parsed_positions,
        )


class AccountReconciler:
    def __init__(self, store: StateStore, snapshot_provider: BinanceRestSnapshotProvider) -> None:
        self.store = store
        self.snapshot_provider = snapshot_provider

    def reconcile(self, activate: bool = True) -> ReconciliationReport:
        self.store.set_execution_ready(False)
        if self.store.halted():
            raise StateMismatchError(self.store.halt_reason() or "HALTED")
        try:
            snapshot = self.snapshot_provider.snapshot()
        except ReconciliationUnavailableError:
            self.store.set_execution_ready(False)
            raise
        except StateMismatchError as exc:
            self.store.halt(str(exc))
            raise
        return self.reconcile_snapshot(snapshot, activate=activate)

    def reconcile_snapshot(self, snapshot: AccountSnapshot, activate: bool = True) -> ReconciliationReport:
        """Compare a pre-fetched snapshot on the StateStore owning thread.

        Network fetches may run in a worker thread, but SQLite mutations and
        comparisons remain on the event-loop thread that owns the store.
        """
        self.store.set_execution_ready(False)
        if self.store.halted():
            raise StateMismatchError(self.store.halt_reason() or "HALTED")
        mismatches = self._compare(snapshot)
        if mismatches:
            reason = "RECONCILIATION_MISMATCH: " + " | ".join(mismatches[:8])
            self.store.halt(reason)
            raise StateMismatchError(reason)
        self.store.set_runtime("last_reconcile_ms", int(time() * 1000))
        self.store.set_execution_ready(activate)
        return ReconciliationReport(
            True,
            len(snapshot.orders),
            len(snapshot.algo_orders),
            len(snapshot.positions),
            (),
        )

    def _compare(self, snapshot: AccountSnapshot) -> tuple[str, ...]:
        local_orders = {o.client_order_id: o for o in self.store.list_orders(active_only=True)}
        remote_orders = {o.client_order_id: o for o in snapshot.orders}
        local_algos = {o.client_algo_id: o for o in self.store.list_algo_orders(active_only=True)}
        remote_algos = {o.client_algo_id: o for o in snapshot.algo_orders}
        local_positions = {p.symbol: p for p in self.store.list_account_positions() if p.quantity != 0}
        remote_positions = {p.symbol: p for p in snapshot.positions}
        mismatches: list[str] = []

        for cid in sorted(set(local_orders) | set(remote_orders)):
            local = local_orders.get(cid)
            remote = remote_orders.get(cid)
            if local is None:
                mismatches.append(f"unknown_remote_order:{cid or remote.exchange_order_id}")
                continue
            if remote is None:
                mismatches.append(f"missing_remote_order:{cid}")
                continue
            mismatches.extend(self._compare_order(local, remote))

        for cid in sorted(set(local_algos) | set(remote_algos)):
            local = local_algos.get(cid)
            remote = remote_algos.get(cid)
            if local is None:
                mismatches.append(f"unknown_remote_algo:{cid or remote.algo_id}")
                continue
            if remote is None:
                mismatches.append(f"missing_remote_algo:{cid}")
                continue
            mismatches.extend(self._compare_algo(local, remote))

        for symbol in sorted(set(local_positions) | set(remote_positions)):
            local = local_positions.get(symbol)
            remote = remote_positions.get(symbol)
            if local is None:
                mismatches.append(f"unknown_remote_position:{symbol}")
                continue
            if remote is None:
                mismatches.append(f"missing_remote_position:{symbol}")
                continue
            if local.direction != remote.direction:
                mismatches.append(f"position_direction:{symbol}:{local.direction}!={remote.direction}")
            if local.quantity != remote.quantity:
                mismatches.append(f"position_qty:{symbol}:{local.quantity}!={remote.quantity}")
            if local.entry_price != remote.entry_price:
                mismatches.append(f"position_entry:{symbol}:{local.entry_price}!={remote.entry_price}")
        return tuple(mismatches)

    @staticmethod
    def _compare_order(local: StoredOrder, remote: ExchangeOrderSnapshot) -> list[str]:
        cid = local.client_order_id
        mismatches: list[str] = []
        pairs = (
            ("exchange_id", local.exchange_order_id, remote.exchange_order_id),
            ("symbol", local.symbol, remote.symbol),
            ("side", local.side, remote.side),
            ("status", local.status, remote.status),
            ("type", local.order_type, remote.order_type),
            ("orig_qty", local.orig_qty, remote.orig_qty),
            ("filled_qty", local.filled_qty, remote.filled_qty),
            ("reduce_only", local.reduce_only, remote.reduce_only),
        )
        for field, left, right in pairs:
            if left != right:
                mismatches.append(f"order_{field}:{cid}:{left}!={right}")
        return mismatches

    @staticmethod
    def _compare_algo(local: StoredAlgoOrder, remote: ExchangeAlgoSnapshot) -> list[str]:
        cid = local.client_algo_id
        mismatches: list[str] = []
        pairs = (
            ("algo_id", local.algo_id, remote.algo_id),
            ("symbol", local.symbol, remote.symbol),
            ("side", local.side, remote.side),
            ("status", local.status, remote.status),
            ("algo_type", local.algo_type, remote.algo_type),
            ("type", local.order_type, remote.order_type),
            ("trigger", local.trigger_price, remote.trigger_price),
            ("qty", local.quantity, remote.quantity),
            ("close_position", local.close_position, remote.close_position),
            ("reduce_only", local.reduce_only, remote.reduce_only),
        )
        if local.actual_order_id and remote.actual_order_id and local.actual_order_id != remote.actual_order_id:
            mismatches.append(f"algo_actual_order_id:{cid}:{local.actual_order_id}!={remote.actual_order_id}")
        for field, left, right in pairs:
            if left != right:
                mismatches.append(f"algo_{field}:{cid}:{left}!={right}")
        return mismatches
