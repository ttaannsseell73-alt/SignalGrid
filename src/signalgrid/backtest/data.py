from __future__ import annotations

import csv
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

from signalgrid.market.state import Bar


class HistoricalDataError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class HistoricalBar:
    symbol: str
    open_time_ms: int
    close_time_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
    taker_buy_quote: float
    best_bid: float | None = None
    best_ask: float | None = None
    bid_depth: float | None = None
    ask_depth: float | None = None

    @property
    def taker_sell_quote(self) -> float:
        return max(0.0, self.quote_volume - self.taker_buy_quote)

    @property
    def has_book(self) -> bool:
        return (
            self.best_bid is not None
            and self.best_ask is not None
            and self.bid_depth is not None
            and self.ask_depth is not None
            and self.best_bid > 0
            and self.best_ask >= self.best_bid
            and self.bid_depth >= 0
            and self.ask_depth >= 0
        )

    def as_bar(self) -> Bar:
        return Bar(self.open, self.high, self.low, self.close, self.volume)


@dataclass(frozen=True, slots=True)
class BookSnapshot:
    symbol: str
    timestamp_ms: int
    best_bid: float
    best_ask: float
    bid_depth: float
    ask_depth: float


@dataclass(frozen=True, slots=True)
class FundingPoint:
    symbol: str
    timestamp_ms: int
    rate: float


BINANCE_KLINE_COLUMNS = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trade_count",
    "taker_buy_base",
    "taker_buy_quote",
    "ignore",
)


def _is_header(row: list[str]) -> bool:
    if not row:
        return False
    try:
        int(float(row[0]))
        return False
    except (ValueError, TypeError):
        return True


def load_binance_klines_csv(path: str | Path, symbol: str) -> list[HistoricalBar]:
    """Load Binance public kline CSV without inventing missing order-book data.

    Supports the canonical 12-column Binance archive layout, with or without a header.
    Book fields intentionally remain None and must be attached from historical snapshots
    before a strict full-core backtest can run.
    """
    out: list[HistoricalBar] = []
    with Path(path).open("r", newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        rows = list(reader)
    if not rows:
        return out
    start = 1 if _is_header(rows[0]) else 0
    header = [h.strip().lower() for h in rows[0]] if start else list(BINANCE_KLINE_COLUMNS)
    index = {name: i for i, name in enumerate(header)}
    aliases = {
        "open_time": ("open_time", "open time", "opentime"),
        "open": ("open",), "high": ("high",), "low": ("low",), "close": ("close",),
        "volume": ("volume",), "close_time": ("close_time", "close time", "closetime"),
        "quote_volume": ("quote_volume", "quote asset volume", "quoteassetvolume"),
        "taker_buy_quote": ("taker_buy_quote", "taker buy quote asset volume", "takerbuyquoteassetvolume"),
    }
    def col(name: str) -> int:
        for alias in aliases[name]:
            if alias in index:
                return index[alias]
        raise HistoricalDataError(f"missing Binance kline column: {name}")
    required = {name: col(name) for name in aliases}
    for raw in rows[start:]:
        if not raw or len(raw) < 11:
            continue
        try:
            out.append(HistoricalBar(
                symbol=symbol.upper(),
                open_time_ms=int(float(raw[required["open_time"]])),
                close_time_ms=int(float(raw[required["close_time"]])),
                open=float(raw[required["open"]]), high=float(raw[required["high"]]),
                low=float(raw[required["low"]]), close=float(raw[required["close"]]),
                volume=float(raw[required["volume"]]),
                quote_volume=float(raw[required["quote_volume"]]),
                taker_buy_quote=float(raw[required["taker_buy_quote"]]),
            ))
        except (ValueError, IndexError) as exc:
            raise HistoricalDataError(f"invalid Binance kline row: {raw[:4]}") from exc
    out.sort(key=lambda x: x.open_time_ms)
    for left, right in zip(out, out[1:]):
        if right.open_time_ms <= left.open_time_ms:
            raise HistoricalDataError("kline timestamps must be strictly increasing")
    return out


def attach_book_snapshots(
    bars: Iterable[HistoricalBar],
    snapshots: Iterable[BookSnapshot],
    *,
    max_age_ms: int = 2_000,
) -> list[HistoricalBar]:
    """Attach only the latest snapshot at-or-before each bar close (never future data)."""
    bars_sorted = sorted(bars, key=lambda x: x.close_time_ms)
    snaps = sorted(snapshots, key=lambda x: x.timestamp_ms)
    out: list[HistoricalBar] = []
    j = 0
    latest: BookSnapshot | None = None
    for bar in bars_sorted:
        while j < len(snaps) and snaps[j].timestamp_ms <= bar.close_time_ms:
            if snaps[j].symbol.upper() == bar.symbol.upper():
                latest = snaps[j]
            j += 1
        if latest is None or bar.close_time_ms - latest.timestamp_ms > max_age_ms:
            out.append(bar)
            continue
        out.append(replace(
            bar,
            best_bid=latest.best_bid,
            best_ask=latest.best_ask,
            bid_depth=latest.bid_depth,
            ask_depth=latest.ask_depth,
        ))
    return out


BOOK_TICKER_COLUMNS = (
    "update_id",
    "best_bid_price",
    "best_bid_qty",
    "best_ask_price",
    "best_ask_qty",
    "transaction_time",
    "event_time",
)


def load_book_ticker_csv(path: str | Path, symbol: str) -> list[BookSnapshot]:
    """Load Binance USD-M futures bookTicker archive rows.

    Canonical archive columns are update_id, best_bid_price, best_bid_qty,
    best_ask_price, best_ask_qty, transaction_time, event_time. Files are
    explicitly sorted because historical Binance archives have had ordering issues.
    """
    with Path(path).open("r", newline="", encoding="utf-8-sig") as fh:
        raw_rows = list(csv.reader(fh))
    if not raw_rows:
        return []
    has_header = _is_header(raw_rows[0])
    header = [h.strip().lower() for h in raw_rows[0]] if has_header else list(BOOK_TICKER_COLUMNS)
    start = 1 if has_header else 0
    idx = {name: i for i, name in enumerate(header)}
    aliases = {
        "bid": ("best_bid_price", "bid_price", "bidprice"),
        "bid_qty": ("best_bid_qty", "bid_qty", "bidqty"),
        "ask": ("best_ask_price", "ask_price", "askprice"),
        "ask_qty": ("best_ask_qty", "ask_qty", "askqty"),
        "event_time": ("event_time", "eventtime", "transaction_time", "transactiontime"),
    }
    def col(key: str) -> int:
        for alias in aliases[key]:
            if alias in idx:
                return idx[alias]
        raise HistoricalDataError(f"missing bookTicker column: {key}")
    cols = {k: col(k) for k in aliases}
    out: list[BookSnapshot] = []
    for raw in raw_rows[start:]:
        if not raw:
            continue
        try:
            out.append(BookSnapshot(
                symbol=symbol.upper(),
                timestamp_ms=int(float(raw[cols["event_time"]])),
                best_bid=float(raw[cols["bid"]]),
                best_ask=float(raw[cols["ask"]]),
                bid_depth=float(raw[cols["bid_qty"]]),
                ask_depth=float(raw[cols["ask_qty"]]),
            ))
        except (ValueError, IndexError) as exc:
            raise HistoricalDataError(f"invalid bookTicker row: {raw[:4]}") from exc
    out.sort(key=lambda x: x.timestamp_ms)
    return out


def load_funding_rate_csv(path: str | Path, symbol: str) -> list[FundingPoint]:
    """Load Binance USD-M fundingRate archive: calc_time, funding_interval_hours, last_funding_rate."""
    with Path(path).open("r", newline="", encoding="utf-8-sig") as fh:
        raw_rows = list(csv.reader(fh))
    if not raw_rows:
        return []
    has_header = _is_header(raw_rows[0])
    header = [h.strip().lower() for h in raw_rows[0]] if has_header else ["calc_time", "funding_interval_hours", "last_funding_rate"]
    start = 1 if has_header else 0
    idx = {name: i for i, name in enumerate(header)}
    try:
        ti = idx["calc_time"]
        ri = idx["last_funding_rate"]
    except KeyError as exc:
        raise HistoricalDataError("fundingRate CSV must contain calc_time and last_funding_rate") from exc
    out: list[FundingPoint] = []
    for raw in raw_rows[start:]:
        if not raw:
            continue
        try:
            out.append(FundingPoint(symbol.upper(), int(float(raw[ti])), float(raw[ri])))
        except (ValueError, IndexError) as exc:
            raise HistoricalDataError(f"invalid fundingRate row: {raw[:3]}") from exc
    out.sort(key=lambda x: x.timestamp_ms)
    return out
