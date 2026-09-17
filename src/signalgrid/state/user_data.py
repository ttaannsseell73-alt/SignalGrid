from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from hashlib import blake2s
from typing import Any

from signalgrid.state.store import StateStore, StoredAlgoOrder, StoredOrder


TERMINAL_ORDER_STATUSES = {"FILLED", "CANCELED", "EXPIRED", "REJECTED", "EXPIRED_IN_MATCH"}
TERMINAL_ALGO_STATUSES = {"TRIGGERED", "FINISHED", "CANCELED", "EXPIRED", "REJECTED", "FAILED"}


class UserDataStateError(RuntimeError):
    pass


class ForeignAccountActivityError(UserDataStateError):
    pass


class UserDataConflictError(UserDataStateError):
    pass


@dataclass(frozen=True, slots=True)
class UserDataResult:
    kind: str
    changed: bool
    duplicate: bool = False
    stale: bool = False
    reconnect_required: bool = False
    detail: str = ""


def _model_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    actual = getattr(value, "actual_instance", None)
    if actual is not None:
        raw = _model_dict(actual)
        event_by_class = {
            "OrderTradeUpdate": "ORDER_TRADE_UPDATE",
            "AccountUpdate": "ACCOUNT_UPDATE",
            "AlgoUpdate": "ALGO_UPDATE",
            "ListenKeyExpired": "listenKeyExpired",
            "MarginCall": "MARGIN_CALL",
            "ConditionalOrderTriggerReject": "CONDITIONAL_ORDER_TRIGGER_REJECT",
            "AccountConfigUpdate": "ACCOUNT_CONFIG_UPDATE",
            "StrategyUpdate": "STRATEGY_UPDATE",
            "GridUpdate": "GRID_UPDATE",
            "TradeLite": "TRADE_LITE",
        }
        raw.setdefault("e", event_by_class.get(type(actual).__name__, type(actual).__name__))
        return raw
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        raw = to_dict()
        if isinstance(raw, dict):
            return raw
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(by_alias=True, exclude_none=True)
    as_dict = getattr(value, "dict", None)
    if callable(as_dict):
        return as_dict(by_alias=True, exclude_none=True)
    return vars(value)


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes"}


def _event_hash(prefix: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return f"{prefix}:{blake2s(encoded, digest_size=16).hexdigest()}"


class BinanceUserDataProcessor:
    """Persist Binance USD-M account events and close execution on ambiguity.

    No strategy decisions are allowed here. V1 deliberately requires one-way
    position mode because the execution adapter does not submit positionSide.
    """

    def __init__(self, store: StateStore, client_id_prefix: str = "sg-") -> None:
        self.store = store
        self.client_id_prefix = client_id_prefix

    def on_message(self, message: Any) -> UserDataResult:
        raw = _model_dict(message)
        event = str(raw.get("e") or raw.get("eventType") or raw.get("event_type") or "")
        if not event:
            if "o" in raw and isinstance(raw.get("o"), dict):
                order = raw["o"]
                if "caid" in order or "aid" in order:
                    event = "ALGO_UPDATE"
                elif "c" in order or "i" in order:
                    event = "ORDER_TRADE_UPDATE"
            elif "a" in raw and isinstance(raw.get("a"), dict) and "P" in raw["a"]:
                event = "ACCOUNT_UPDATE"

        if event == "ORDER_TRADE_UPDATE":
            return self._order_update(raw)
        if event == "ALGO_UPDATE":
            return self._algo_update(raw)
        if event == "ACCOUNT_UPDATE":
            return self._account_update(raw)
        if event in {"listenKeyExpired", "LISTEN_KEY_EXPIRED"}:
            self.store.set_execution_ready(False)
            self.store.set_runtime("user_stream_status", "EXPIRED")
            return UserDataResult(event, True, reconnect_required=True, detail="LISTEN_KEY_EXPIRED")
        if event in {"MARGIN_CALL", "marginCall"}:
            reason = "MARGIN_CALL"
            self.store.halt(reason)
            return UserDataResult(event, True, detail=reason)
        if event in {"CONDITIONAL_ORDER_TRIGGER_REJECT"}:
            reason = "CONDITIONAL_ORDER_TRIGGER_REJECT"
            self.store.halt(reason)
            return UserDataResult(event, True, detail=reason)
        return UserDataResult(event or "UNKNOWN", False, detail="IGNORED")

    def _order_update(self, raw: dict[str, Any]) -> UserDataResult:
        o = _model_dict(raw.get("o") or {})
        event_time = int(o.get("T") or raw.get("T") or raw.get("E") or 0)
        client_id = str(o.get("c") or "")
        exchange_id = str(o.get("i") or "")
        symbol = str(o.get("s") or "").upper()
        side = str(o.get("S") or "").upper()
        status = str(o.get("X") or "").upper()
        order_type = str(o.get("o") or o.get("ot") or "").upper()
        execution_type = str(o.get("x") or "").upper()
        position_side = str(o.get("ps") or "BOTH").upper()

        if position_side != "BOTH":
            return self._halt_conflict(f"HEDGE_MODE_UNSUPPORTED:{symbol}:{position_side}", "ORDER_TRADE_UPDATE")
        if client_id.startswith("autoclose-") or client_id == "adl_autoclose":
            return self._halt_conflict(f"FORCED_CLOSE_EVENT:{symbol}:{client_id}", "ORDER_TRADE_UPDATE")

        existing = self.store.get_order(client_id) if client_id else None
        if existing is None and exchange_id:
            existing = self.store.get_order_by_exchange_id(exchange_id)
        if not client_id or (existing is None and not client_id.startswith(self.client_id_prefix)):
            return self._halt_foreign(f"FOREIGN_ORDER_EVENT:{symbol}:{client_id or exchange_id}", "ORDER_TRADE_UPDATE")

        incoming = StoredOrder(
            client_order_id=client_id or existing.client_order_id,
            exchange_order_id=exchange_id,
            symbol=symbol,
            side=side,
            status=status,
            order_type=order_type,
            orig_qty=Decimal(str(o.get("q") or "0")),
            filled_qty=Decimal(str(o.get("z") or "0")),
            avg_price=Decimal(str(o.get("ap") or "0")),
            reduce_only=_bool(o.get("R")),
            event_time=event_time,
            last_trade_id=None if o.get("t") is None or int(o.get("t")) < 0 else int(o.get("t")),
        )

        if existing is not None:
            conflict = self._order_identity_conflict(existing, incoming)
            if conflict:
                return self._halt_conflict(conflict, "ORDER_TRADE_UPDATE")
            if event_time and event_time < existing.event_time:
                return UserDataResult("ORDER_TRADE_UPDATE", False, stale=True, detail="STALE_ORDER_EVENT")
            if incoming.filled_qty < existing.filled_qty:
                return self._halt_conflict(
                    f"ORDER_FILL_REGRESSION:{client_id}:{incoming.filled_qty}<{existing.filled_qty}",
                    "ORDER_TRADE_UPDATE",
                )
            if existing.status in TERMINAL_ORDER_STATUSES and incoming.status != existing.status:
                return self._halt_conflict(
                    f"TERMINAL_ORDER_MUTATION:{client_id}:{existing.status}->{incoming.status}",
                    "ORDER_TRADE_UPDATE",
                )
            if incoming == existing:
                return UserDataResult("ORDER_TRADE_UPDATE", False, duplicate=True, detail="IDENTICAL_ORDER_EVENT")

        event_key = _event_hash(
            "order",
            {
                "E": raw.get("E"),
                "T": raw.get("T"),
                "i": exchange_id,
                "c": client_id,
                "x": execution_type,
                "X": status,
                "z": str(incoming.filled_qty),
                "t": incoming.last_trade_id,
            },
        )
        if not self.store.mark_event_once(event_key, event_time):
            return UserDataResult("ORDER_TRADE_UPDATE", False, duplicate=True, detail="DUPLICATE_ORDER_EVENT")
        self.store.upsert_order(incoming)
        return UserDataResult("ORDER_TRADE_UPDATE", True, detail=status or execution_type)

    def _algo_update(self, raw: dict[str, Any]) -> UserDataResult:
        o = _model_dict(raw.get("o") or {})
        event_time = int(raw.get("T") or raw.get("E") or 0)
        client_id = str(o.get("caid") or "")
        algo_id = str(o.get("aid") or "")
        symbol = str(o.get("s") or "").upper()
        position_side = str(o.get("ps") or "BOTH").upper()
        if position_side != "BOTH":
            return self._halt_conflict(f"HEDGE_MODE_UNSUPPORTED:{symbol}:{position_side}", "ALGO_UPDATE")

        existing = self.store.get_algo_order(client_id) if client_id else None
        if existing is None and algo_id:
            existing = self.store.get_algo_order_by_algo_id(algo_id)
        if not client_id or (existing is None and not client_id.startswith(self.client_id_prefix)):
            return self._halt_foreign(f"FOREIGN_ALGO_EVENT:{symbol}:{client_id or algo_id}", "ALGO_UPDATE")

        incoming = StoredAlgoOrder(
            client_algo_id=client_id or existing.client_algo_id,
            algo_id=algo_id,
            symbol=symbol,
            side=str(o.get("S") or "").upper(),
            status=str(o.get("X") or "").upper(),
            algo_type=str(o.get("at") or "").upper(),
            order_type=str(o.get("o") or "").upper(),
            trigger_price=Decimal(str(o.get("tp") or "0")),
            quantity=Decimal(str(o.get("q") or "0")),
            close_position=_bool(o.get("cp")),
            reduce_only=_bool(o.get("R")),
            actual_order_id=str(o.get("ai") or ""),
            event_time=event_time,
        )

        if existing is not None:
            conflict = self._algo_identity_conflict(existing, incoming)
            if conflict:
                return self._halt_conflict(conflict, "ALGO_UPDATE")
            if event_time and event_time < existing.event_time:
                return UserDataResult("ALGO_UPDATE", False, stale=True, detail="STALE_ALGO_EVENT")
            if existing.status in TERMINAL_ALGO_STATUSES and incoming.status != existing.status:
                return self._halt_conflict(
                    f"TERMINAL_ALGO_MUTATION:{client_id}:{existing.status}->{incoming.status}",
                    "ALGO_UPDATE",
                )
            if incoming == existing:
                return UserDataResult("ALGO_UPDATE", False, duplicate=True, detail="IDENTICAL_ALGO_EVENT")

        event_key = _event_hash(
            "algo",
            {
                "E": raw.get("E"),
                "T": raw.get("T"),
                "aid": algo_id,
                "caid": client_id,
                "X": incoming.status,
                "ai": incoming.actual_order_id,
            },
        )
        if not self.store.mark_event_once(event_key, event_time):
            return UserDataResult("ALGO_UPDATE", False, duplicate=True, detail="DUPLICATE_ALGO_EVENT")
        self.store.upsert_algo_order(incoming)
        return UserDataResult("ALGO_UPDATE", True, detail=incoming.status)

    def _account_update(self, raw: dict[str, Any]) -> UserDataResult:
        event_time = int(raw.get("T") or raw.get("E") or 0)
        a = _model_dict(raw.get("a") or {})
        positions = a.get("P") or []
        if not isinstance(positions, list):
            positions = list(positions)
        event_key = _event_hash("account", {"E": raw.get("E"), "T": raw.get("T"), "P": positions})
        if not self.store.mark_event_once(event_key, event_time):
            return UserDataResult("ACCOUNT_UPDATE", False, duplicate=True, detail="DUPLICATE_ACCOUNT_EVENT")

        changed = False
        for item in positions:
            p = _model_dict(item)
            symbol = str(p.get("s") or "").upper()
            if not symbol:
                continue
            position_side = str(p.get("ps") or "BOTH").upper()
            if position_side != "BOTH":
                return self._halt_conflict(f"HEDGE_MODE_UNSUPPORTED:{symbol}:{position_side}", "ACCOUNT_UPDATE")
            qty_signed = Decimal(str(p.get("pa") or "0"))
            entry_price = Decimal(str(p.get("ep") or "0"))
            current = self.store.get_account_position(symbol)
            if current is not None and event_time and event_time < current.event_time:
                continue
            if qty_signed == 0:
                if current is not None:
                    self.store.delete_position(symbol)
                    changed = True
                continue
            direction = "LONG" if qty_signed > 0 else "SHORT"
            qty = abs(qty_signed)
            notional = qty * entry_price
            self.store.upsert_account_position(symbol, direction, qty, entry_price, notional, event_time)
            changed = True
        return UserDataResult("ACCOUNT_UPDATE", changed, detail="POSITION_STATE_UPDATED" if changed else "NO_POSITION_CHANGE")

    @staticmethod
    def _order_identity_conflict(existing: StoredOrder, incoming: StoredOrder) -> str | None:
        immutable = (
            ("exchange_id", existing.exchange_order_id, incoming.exchange_order_id),
            ("symbol", existing.symbol, incoming.symbol),
            ("side", existing.side, incoming.side),
            ("type", existing.order_type, incoming.order_type),
            ("orig_qty", existing.orig_qty, incoming.orig_qty),
            ("reduce_only", existing.reduce_only, incoming.reduce_only),
        )
        for field, left, right in immutable:
            if left != right:
                return f"ORDER_IDENTITY_CONFLICT:{existing.client_order_id}:{field}:{left}!={right}"
        return None

    @staticmethod
    def _algo_identity_conflict(existing: StoredAlgoOrder, incoming: StoredAlgoOrder) -> str | None:
        immutable = (
            ("algo_id", existing.algo_id, incoming.algo_id),
            ("symbol", existing.symbol, incoming.symbol),
            ("side", existing.side, incoming.side),
            ("algo_type", existing.algo_type, incoming.algo_type),
            ("type", existing.order_type, incoming.order_type),
            ("trigger", existing.trigger_price, incoming.trigger_price),
            ("qty", existing.quantity, incoming.quantity),
            ("close_position", existing.close_position, incoming.close_position),
            ("reduce_only", existing.reduce_only, incoming.reduce_only),
        )
        for field, left, right in immutable:
            if left != right:
                return f"ALGO_IDENTITY_CONFLICT:{existing.client_algo_id}:{field}:{left}!={right}"
        return None

    def _halt_foreign(self, reason: str, kind: str) -> UserDataResult:
        self.store.halt(reason)
        return UserDataResult(kind, True, detail=reason)

    def _halt_conflict(self, reason: str, kind: str) -> UserDataResult:
        self.store.halt(reason)
        return UserDataResult(kind, True, detail=reason)
