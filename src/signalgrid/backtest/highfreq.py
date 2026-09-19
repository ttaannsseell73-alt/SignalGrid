from __future__ import annotations

import csv
from dataclasses import dataclass
from itertools import chain
from pathlib import Path
from typing import Iterable, Iterator

from signalgrid.backtest.data import HistoricalBar, HistoricalDataError, normalize_timestamp_ms


@dataclass(frozen=True, slots=True)
class AggTrade:
    symbol: str
    agg_trade_id: int
    price: float
    quantity: float
    timestamp_ms: int
    buyer_is_maker: bool

    @property
    def quote_quantity(self) -> float:
        return self.price * self.quantity

    @property
    def taker_buy_quote(self) -> float:
        # Binance m=true means buyer is maker => aggressive side is sell.
        return 0.0 if self.buyer_is_maker else self.quote_quantity


AGGTRADE_COLUMNS = (
    "agg_trade_id",
    "price",
    "quantity",
    "first_trade_id",
    "last_trade_id",
    "transact_time",
    "is_buyer_maker",
)


def _is_header(row: list[str]) -> bool:
    if not row:
        return False
    try:
        int(float(row[0]))
        return False
    except (TypeError, ValueError):
        return True


def _parse_bool(value: str) -> bool:
    text = str(value).strip().lower()
    if text in {"true", "1", "t", "yes"}:
        return True
    if text in {"false", "0", "f", "no"}:
        return False
    raise HistoricalDataError(f"invalid boolean value: {value!r}")


def iter_binance_aggtrades_csv(
    path: str | Path,
    symbol: str,
) -> Iterator[AggTrade]:
    with Path(path).open("r", newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        first = next(reader, None)
        if first is None:
            return

        if _is_header(first):
            header = [x.strip().lower() for x in first]
            rows: Iterable[list[str]] = reader
        else:
            header = list(AGGTRADE_COLUMNS)
            rows = chain((first,), reader)

        index = {name: i for i, name in enumerate(header)}

        def col(*aliases: str) -> int:
            for alias in aliases:
                if alias in index:
                    return index[alias]
            raise HistoricalDataError(f"missing aggTrades column: {aliases}")

        ai = col("agg_trade_id", "aggtradeid", "a")
        pi = col("price", "p")
        qi = col("quantity", "qty", "q")
        ti = col("transact_time", "transaction_time", "time", "timestamp", "t")
        mi = col("is_buyer_maker", "buyer_is_maker", "m")

        last_id: int | None = None
        last_ts: int | None = None
        for raw in rows:
            if not raw:
                continue
            try:
                trade = AggTrade(
                    symbol=symbol.upper(),
                    agg_trade_id=int(float(raw[ai])),
                    price=float(raw[pi]),
                    quantity=float(raw[qi]),
                    timestamp_ms=normalize_timestamp_ms(raw[ti]),
                    buyer_is_maker=_parse_bool(raw[mi]),
                )
            except (ValueError, IndexError) as exc:
                raise HistoricalDataError(f"invalid aggTrades row: {raw[:7]}") from exc
            if trade.price <= 0 or trade.quantity <= 0:
                raise HistoricalDataError("aggTrades price/quantity must be positive")
            if last_id is not None and trade.agg_trade_id <= last_id:
                raise HistoricalDataError("aggTrades ids must be strictly increasing")
            if last_ts is not None and trade.timestamp_ms < last_ts:
                raise HistoricalDataError("aggTrades timestamps must be non-decreasing")
            last_id = trade.agg_trade_id
            last_ts = trade.timestamp_ms
            yield trade


def aggregate_aggtrades(
    trades: Iterable[AggTrade],
    *,
    bucket_ms: int = 5_000,
    fill_gaps: bool = True,
) -> list[HistoricalBar]:
    if bucket_ms <= 0:
        raise ValueError("bucket_ms must be positive")

    out: list[HistoricalBar] = []
    current_bucket: int | None = None
    open_price = high = low = close = 0.0
    volume = quote_volume = taker_buy_quote = 0.0
    symbol = ""
    previous_close: float | None = None

    def emit(bucket: int) -> None:
        nonlocal previous_close
        out.append(
            HistoricalBar(
                symbol=symbol,
                open_time_ms=bucket,
                close_time_ms=bucket + bucket_ms - 1,
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=volume,
                quote_volume=quote_volume,
                taker_buy_quote=taker_buy_quote,
            )
        )
        previous_close = close

    for trade in trades:
        bucket = (trade.timestamp_ms // bucket_ms) * bucket_ms
        if current_bucket is None:
            current_bucket = bucket
            symbol = trade.symbol
            open_price = high = low = close = trade.price
            volume = trade.quantity
            quote_volume = trade.quote_quantity
            taker_buy_quote = trade.taker_buy_quote
            continue

        if trade.symbol != symbol:
            raise HistoricalDataError("aggregate_aggtrades accepts one symbol at a time")

        if bucket != current_bucket:
            emit(current_bucket)
            if fill_gaps and previous_close is not None:
                gap = current_bucket + bucket_ms
                while gap < bucket:
                    out.append(
                        HistoricalBar(
                            symbol=symbol,
                            open_time_ms=gap,
                            close_time_ms=gap + bucket_ms - 1,
                            open=previous_close,
                            high=previous_close,
                            low=previous_close,
                            close=previous_close,
                            volume=0.0,
                            quote_volume=0.0,
                            taker_buy_quote=0.0,
                        )
                    )
                    gap += bucket_ms
            current_bucket = bucket
            open_price = high = low = close = trade.price
            volume = trade.quantity
            quote_volume = trade.quote_quantity
            taker_buy_quote = trade.taker_buy_quote
            continue

        high = max(high, trade.price)
        low = min(low, trade.price)
        close = trade.price
        volume += trade.quantity
        quote_volume += trade.quote_quantity
        taker_buy_quote += trade.taker_buy_quote

    if current_bucket is not None:
        emit(current_bucket)
    return out


def write_historical_bars_csv(
    bars: Iterable[HistoricalBar],
    path: str | Path,
) -> int:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with destination.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        for bar in bars:
            writer.writerow(
                [
                    bar.open_time_ms,
                    bar.open,
                    bar.high,
                    bar.low,
                    bar.close,
                    bar.volume,
                    bar.close_time_ms,
                    bar.quote_volume,
                    0,
                    0,
                    bar.taker_buy_quote,
                    0,
                ]
            )
            count += 1
    return count
