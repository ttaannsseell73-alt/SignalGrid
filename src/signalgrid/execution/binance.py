from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from hashlib import blake2s
from typing import Any, Callable

from signalgrid.models import Direction


class ExecutionError(RuntimeError):
    code = "EXECUTION_ERROR"


class InvalidIntentError(ExecutionError):
    code = "INVALID_INTENT"


class SymbolRulesError(ExecutionError):
    code = "SYMBOL_RULES"


class BinanceRejectedError(ExecutionError):
    code = "BINANCE_REJECTED"


class BinanceRateLimitError(ExecutionError):
    code = "BINANCE_RATE_LIMIT"


class BinanceAuthError(ExecutionError):
    code = "BINANCE_AUTH"


class BinanceNetworkError(ExecutionError):
    code = "BINANCE_NETWORK"


class ExecutionBlockedError(ExecutionError):
    code = "EXECUTION_BLOCKED"


@dataclass(frozen=True, slots=True)
class SymbolRules:
    step_size: Decimal
    min_qty: Decimal
    tick_size: Decimal
    min_notional: Decimal


@dataclass(slots=True)
class OrderIntent:
    symbol: str
    direction: Direction
    notional_usdt: float
    leverage: int
    invalidation: float | None
    idempotency_key: str = ""
    reference_price: float | None = None


@dataclass(frozen=True, slots=True)
class LimitEntryIntent:
    symbol: str
    direction: Direction
    notional_usdt: float
    price: float
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ReduceOnlyMarketIntent:
    symbol: str
    direction: Direction
    quantity: Decimal | float | str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ExecutionReceipt:
    symbol: str
    client_order_id: str
    exchange_order_id: str
    quantity: Decimal
    reference_price: Decimal


@dataclass(frozen=True, slots=True)
class ProtectiveExitIntent:
    symbol: str
    direction: Direction
    trigger_price: float
    idempotency_key: str
    close_position: bool = True
    order_type: str = "STOP_MARKET"


def _d(value: Any) -> Decimal:
    return Decimal(str(value))


def round_down(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        raise SymbolRulesError("step must be positive")
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def client_order_id(prefix: str, key: str) -> str:
    if not key:
        raise InvalidIntentError("idempotency_key is required")
    digest = blake2s(key.encode("utf-8"), digest_size=10).hexdigest()
    return f"sg-{prefix}-{digest}"[:36]


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


def parse_symbol_rules(exchange_info: Any, symbol: str) -> SymbolRules:
    data = _unwrap(exchange_info)
    symbols = data.get("symbols", []) if isinstance(data, dict) else getattr(data, "symbols", [])
    target: dict[str, Any] | None = None
    for item in symbols:
        raw = _model_dict(item)
        if raw.get("symbol") == symbol.upper():
            target = raw
            break
    if target is None:
        raise SymbolRulesError(f"unknown symbol: {symbol}")

    filters = {_model_dict(item).get("filterType"): _model_dict(item) for item in target.get("filters", [])}
    lot = filters.get("LOT_SIZE") or filters.get("MARKET_LOT_SIZE")
    price = filters.get("PRICE_FILTER")
    minimum = filters.get("MIN_NOTIONAL", {})
    if not lot or not price:
        raise SymbolRulesError(f"incomplete filters for {symbol}")
    return SymbolRules(
        step_size=_d(lot["stepSize"]),
        min_qty=_d(lot["minQty"]),
        tick_size=_d(price["tickSize"]),
        min_notional=_d(minimum.get("notional", "0")),
    )


class BinanceExecutionPort:
    """Small boundary around Binance. No strategy logic is allowed here."""

    async def place_entry(self, intent: OrderIntent) -> str:
        raise NotImplementedError

    async def place_limit_entry_receipt(self, intent: LimitEntryIntent) -> ExecutionReceipt:
        raise NotImplementedError

    async def place_protective_exit(self, intent: ProtectiveExitIntent) -> str:
        raise NotImplementedError

    async def place_reduce_only_market(self, intent: ReduceOnlyMarketIntent) -> str:
        raise NotImplementedError

    async def cancel_order(self, symbol: str, order_id: str) -> None:
        raise NotImplementedError

    async def cancel_algo_order(self, client_algo_id: str) -> None:
        raise NotImplementedError


class BinanceRestExecutionAdapter(BinanceExecutionPort):
    """Authenticated USD-M execution adapter around Binance's official REST SDK surface."""

    def __init__(
        self,
        rest_api: Any,
        price_provider: Callable[[str], float],
        exchange_info_provider: Callable[[], Any] | None = None,
        execution_gate: Callable[[], bool] | None = None,
    ) -> None:
        self.rest_api = rest_api
        self.price_provider = price_provider
        self.exchange_info_provider = exchange_info_provider or rest_api.exchange_information
        self.execution_gate = execution_gate
        self._rules: dict[str, SymbolRules] = {}

    def _rules_for(self, symbol: str) -> SymbolRules:
        symbol = symbol.upper()
        if symbol not in self._rules:
            self._rules[symbol] = parse_symbol_rules(self.exchange_info_provider(), symbol)
        return self._rules[symbol]

    def _quantity_for_notional(self, symbol: str, notional_usdt: float, price: Decimal) -> Decimal:
        rules = self._rules_for(symbol)
        quantity = round_down(_d(notional_usdt) / price, rules.step_size)
        if quantity < rules.min_qty:
            raise InvalidIntentError("quantity below minQty after rounding")
        if rules.min_notional and quantity * price < rules.min_notional:
            raise InvalidIntentError("notional below MIN_NOTIONAL after rounding")
        return quantity

    async def place_entry(self, intent: OrderIntent) -> str:
        return (await self.place_entry_receipt(intent)).client_order_id

    async def place_entry_receipt(self, intent: OrderIntent) -> ExecutionReceipt:
        self._validate(intent)
        self._ensure_entry_ready()
        symbol = intent.symbol.upper()
        reference_price = _d(
            intent.reference_price if intent.reference_price is not None else self.price_provider(symbol)
        )
        if reference_price <= 0:
            raise InvalidIntentError("reference price must be positive")
        quantity = self._quantity_for_notional(symbol, intent.notional_usdt, reference_price)
        cid = client_order_id("e", intent.idempotency_key)
        side = "BUY" if intent.direction is Direction.LONG else "SELL"
        try:
            response = self.rest_api.new_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=float(quantity),
                new_client_order_id=cid,
                new_order_resp_type="RESULT",
            )
        except Exception as exc:
            raise map_binance_error(exc) from exc
        data = _model_dict(_unwrap(response))
        exchange_order_id = str(data.get("orderId") or data.get("order_id") or "")
        return ExecutionReceipt(symbol, cid, exchange_order_id, quantity, reference_price)

    async def place_limit_entry_receipt(self, intent: LimitEntryIntent) -> ExecutionReceipt:
        if intent.direction not in (Direction.LONG, Direction.SHORT):
            raise InvalidIntentError("limit entry direction must be LONG or SHORT")
        if intent.notional_usdt <= 0 or intent.price <= 0 or not intent.idempotency_key:
            raise InvalidIntentError("invalid limit entry intent")
        self._ensure_entry_ready()
        symbol = intent.symbol.upper()
        rules = self._rules_for(symbol)
        price = round_down(_d(intent.price), rules.tick_size)
        if price <= 0:
            raise InvalidIntentError("limit price invalid after rounding")
        quantity = self._quantity_for_notional(symbol, intent.notional_usdt, price)
        cid = client_order_id("g", intent.idempotency_key)
        side = "BUY" if intent.direction is Direction.LONG else "SELL"
        try:
            response = self.rest_api.new_order(
                symbol=symbol,
                side=side,
                type="LIMIT",
                time_in_force="GTC",
                quantity=float(quantity),
                price=float(price),
                new_client_order_id=cid,
                new_order_resp_type="RESULT",
            )
        except Exception as exc:
            raise map_binance_error(exc) from exc
        data = _model_dict(_unwrap(response))
        exchange_order_id = str(data.get("orderId") or data.get("order_id") or "")
        return ExecutionReceipt(symbol, cid, exchange_order_id, quantity, price)

    async def place_protective_exit(self, intent: ProtectiveExitIntent) -> str:
        if intent.direction is Direction.PASS or intent.trigger_price <= 0:
            raise InvalidIntentError("invalid protective exit")
        order_type = intent.order_type.upper()
        if order_type not in {"STOP_MARKET", "TAKE_PROFIT_MARKET"}:
            raise InvalidIntentError("protective order_type must be STOP_MARKET or TAKE_PROFIT_MARKET")
        symbol = intent.symbol.upper()
        rules = self._rules_for(symbol)
        trigger = round_down(_d(intent.trigger_price), rules.tick_size)
        cid = client_order_id("x", intent.idempotency_key)
        side = "SELL" if intent.direction is Direction.LONG else "BUY"
        try:
            response = self.rest_api.new_algo_order(
                algo_type="CONDITIONAL",
                symbol=symbol,
                side=side,
                type=order_type,
                trigger_price=float(trigger),
                close_position="true" if intent.close_position else "false",
                client_algo_id=cid,
                working_type="MARK_PRICE",
            )
        except Exception as exc:
            raise map_binance_error(exc) from exc
        data = _model_dict(_unwrap(response))
        return str(data.get("clientAlgoId") or data.get("client_algo_id") or cid)

    async def place_reduce_only_market(self, intent: ReduceOnlyMarketIntent) -> str:
        if intent.direction not in (Direction.LONG, Direction.SHORT):
            raise InvalidIntentError("reduce-only direction must be LONG or SHORT")
        if not intent.idempotency_key:
            raise InvalidIntentError("idempotency_key is required")
        symbol = intent.symbol.upper()
        rules = self._rules_for(symbol)
        quantity = round_down(_d(intent.quantity), rules.step_size)
        if quantity < rules.min_qty:
            raise InvalidIntentError("reduce-only quantity below minQty")
        cid = client_order_id("r", intent.idempotency_key)
        side = "SELL" if intent.direction is Direction.LONG else "BUY"
        try:
            self.rest_api.new_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=float(quantity),
                reduce_only="true",
                new_client_order_id=cid,
                new_order_resp_type="RESULT",
            )
        except Exception as exc:
            raise map_binance_error(exc) from exc
        return cid

    async def cancel_order(self, symbol: str, order_id: str) -> None:
        try:
            self.rest_api.cancel_order(symbol=symbol.upper(), order_id=int(order_id))
        except Exception as exc:
            raise map_binance_error(exc) from exc

    async def cancel_algo_order(self, client_algo_id: str) -> None:
        if not client_algo_id:
            raise InvalidIntentError("client_algo_id is required")
        try:
            self.rest_api.cancel_algo_order(client_algo_id=client_algo_id)
        except Exception as exc:
            raise map_binance_error(exc) from exc

    def _ensure_entry_ready(self) -> None:
        if self.execution_gate is None:
            raise ExecutionBlockedError("execution gate is not configured")
        if not self.execution_gate():
            raise ExecutionBlockedError("execution is not ready; reconciliation/user-stream gate is closed")

    @staticmethod
    def _validate(intent: OrderIntent) -> None:
        if intent.direction not in (Direction.LONG, Direction.SHORT):
            raise InvalidIntentError("entry direction must be LONG or SHORT")
        if intent.notional_usdt <= 0:
            raise InvalidIntentError("notional_usdt must be positive")
        if intent.leverage < 1:
            raise InvalidIntentError("leverage must be >= 1")
        if not intent.idempotency_key:
            raise InvalidIntentError("idempotency_key is required")


def map_binance_error(exc: Exception) -> ExecutionError:
    name = type(exc).__name__.lower()
    text = str(exc)
    if "toomanyrequests" in name or "ratelimit" in name or "429" in text:
        return BinanceRateLimitError(text)
    if "unauthorized" in name or "forbidden" in name or "401" in text or "403" in text:
        return BinanceAuthError(text)
    if "network" in name or "timeout" in name or "connection" in name:
        return BinanceNetworkError(text)
    return BinanceRejectedError(text)
