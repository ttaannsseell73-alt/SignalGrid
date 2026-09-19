from __future__ import annotations

import argparse
import asyncio
import json
import math
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import websockets


PUBLIC_STREAM_BASE = "wss://fstream.binance.com/public/stream?streams="
MARKET_STREAM_BASE = "wss://fstream.binance.com/market/stream?streams="


def _imbalance(bid: float, ask: float) -> float:
    total = bid + ask
    return (bid - ask) / total if total > 0 else 0.0


def _notional(levels: list[list[str]] | list[tuple[str, str]], n: int) -> float:
    total = 0.0
    for price, qty, *_ in levels[:n]:
        total += float(price) * float(qty)
    return total


def _microprice(bid: float, bid_qty: float, ask: float, ask_qty: float) -> float | None:
    total = bid_qty + ask_qty
    if bid <= 0 or ask <= 0 or total <= 0:
        return None
    return (ask * bid_qty + bid * ask_qty) / total


@dataclass(slots=True)
class ShadowAccumulator:
    symbol: str
    bucket_ms: int = 1_000
    bucket_start_ms: int | None = None
    buy_quote: float = 0.0
    sell_quote: float = 0.0
    trade_count: int = 0
    trade_high: float | None = None
    trade_low: float | None = None
    last_price: float | None = None
    best_bid: float | None = None
    best_ask: float | None = None
    best_bid_qty: float = 0.0
    best_ask_qty: float = 0.0
    depth_bid_5: float = 0.0
    depth_ask_5: float = 0.0
    depth_bid_10: float = 0.0
    depth_ask_10: float = 0.0
    depth_bid_20: float = 0.0
    depth_ask_20: float = 0.0
    last_event_ms: int | None = None

    def _ensure_bucket(self, event_ms: int) -> dict[str, Any] | None:
        bucket = event_ms - (event_ms % self.bucket_ms)
        if self.bucket_start_ms is None:
            self.bucket_start_ms = bucket
            return None
        if bucket <= self.bucket_start_ms:
            return None
        row = self.snapshot()
        self.bucket_start_ms = bucket
        self.buy_quote = 0.0
        self.sell_quote = 0.0
        self.trade_count = 0
        self.trade_high = None
        self.trade_low = None
        return row

    def on_agg_trade(
        self,
        *,
        event_ms: int,
        price: float,
        qty: float,
        buyer_is_maker: bool,
    ) -> dict[str, Any] | None:
        row = self._ensure_bucket(event_ms)
        quote = price * qty
        if buyer_is_maker:
            self.sell_quote += quote
        else:
            self.buy_quote += quote
        self.trade_count += 1
        self.trade_high = price if self.trade_high is None else max(self.trade_high, price)
        self.trade_low = price if self.trade_low is None else min(self.trade_low, price)
        self.last_price = price
        self.last_event_ms = event_ms
        return row

    def on_book_ticker(
        self,
        *,
        event_ms: int,
        bid: float,
        bid_qty: float,
        ask: float,
        ask_qty: float,
    ) -> dict[str, Any] | None:
        row = self._ensure_bucket(event_ms)
        self.best_bid = bid
        self.best_ask = ask
        self.best_bid_qty = bid_qty
        self.best_ask_qty = ask_qty
        self.last_event_ms = event_ms
        return row

    def on_depth(
        self,
        *,
        event_ms: int,
        bids: list[list[str]],
        asks: list[list[str]],
    ) -> dict[str, Any] | None:
        row = self._ensure_bucket(event_ms)
        self.depth_bid_5 = _notional(bids, 5)
        self.depth_ask_5 = _notional(asks, 5)
        self.depth_bid_10 = _notional(bids, 10)
        self.depth_ask_10 = _notional(asks, 10)
        self.depth_bid_20 = _notional(bids, 20)
        self.depth_ask_20 = _notional(asks, 20)
        self.last_event_ms = event_ms
        return row

    def snapshot(self) -> dict[str, Any]:
        bid = float(self.best_bid or 0.0)
        ask = float(self.best_ask or 0.0)
        mid = (bid + ask) / 2.0 if bid > 0 and ask >= bid else 0.0
        spread_bps = ((ask - bid) / mid * 10_000.0) if mid > 0 else None
        micro = _microprice(
            bid,
            self.best_bid_qty,
            ask,
            self.best_ask_qty,
        )
        micro_bps = ((micro - mid) / mid * 10_000.0) if micro is not None and mid > 0 else None
        flow = _imbalance(self.buy_quote, self.sell_quote)
        trade_range_bps = None
        if (
            self.trade_high is not None
            and self.trade_low is not None
            and self.last_price is not None
            and self.last_price > 0
        ):
            trade_range_bps = (
                (self.trade_high - self.trade_low) / self.last_price * 10_000.0
            )

        return {
            "bucket_start_ms": self.bucket_start_ms,
            "symbol": self.symbol,
            "last_event_ms": self.last_event_ms,
            "last_price": self.last_price,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "spread_bps": spread_bps,
            "microprice_bps": micro_bps,
            "buy_quote": self.buy_quote,
            "sell_quote": self.sell_quote,
            "flow_imbalance": flow,
            "trade_count": self.trade_count,
            "trade_range_bps": trade_range_bps,
            "depth_imbalance_5": _imbalance(self.depth_bid_5, self.depth_ask_5),
            "depth_imbalance_10": _imbalance(self.depth_bid_10, self.depth_ask_10),
            "depth_imbalance_20": _imbalance(self.depth_bid_20, self.depth_ask_20),
            "depth_bid_5": self.depth_bid_5,
            "depth_ask_5": self.depth_ask_5,
            "depth_bid_10": self.depth_bid_10,
            "depth_ask_10": self.depth_ask_10,
            "depth_bid_20": self.depth_bid_20,
            "depth_ask_20": self.depth_ask_20,
        }


class ShadowStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS microstructure_1s (
                bucket_start_ms INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                last_event_ms INTEGER,
                last_price REAL,
                best_bid REAL,
                best_ask REAL,
                spread_bps REAL,
                microprice_bps REAL,
                buy_quote REAL NOT NULL,
                sell_quote REAL NOT NULL,
                flow_imbalance REAL NOT NULL,
                trade_count INTEGER NOT NULL,
                trade_range_bps REAL,
                depth_imbalance_5 REAL NOT NULL,
                depth_imbalance_10 REAL NOT NULL,
                depth_imbalance_20 REAL NOT NULL,
                depth_bid_5 REAL NOT NULL,
                depth_ask_5 REAL NOT NULL,
                depth_bid_10 REAL NOT NULL,
                depth_ask_10 REAL NOT NULL,
                depth_bid_20 REAL NOT NULL,
                depth_ask_20 REAL NOT NULL,
                PRIMARY KEY (bucket_start_ms, symbol)
            )
            """
        )
        self.conn.commit()

    def write(self, row: dict[str, Any] | None) -> None:
        if row is None or row.get("bucket_start_ms") is None:
            return
        columns = list(row)
        placeholders = ",".join("?" for _ in columns)
        self.conn.execute(
            f"INSERT OR REPLACE INTO microstructure_1s ({','.join(columns)}) VALUES ({placeholders})",
            [row[c] for c in columns],
        )

    def commit(self) -> None:
        self.conn.commit()

    def count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM microstructure_1s").fetchone()[0])

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()


def public_stream_names(symbols: tuple[str, ...]) -> list[str]:
    streams: list[str] = []
    for symbol in symbols:
        s = symbol.lower()
        streams.extend([f"{s}@bookTicker", f"{s}@depth20@100ms"])
    return streams


def market_stream_names(symbols: tuple[str, ...]) -> list[str]:
    return [f"{symbol.lower()}@aggTrade" for symbol in symbols]


def combined_url(base: str, streams: list[str]) -> str:
    if not streams:
        raise ValueError("at least one stream is required")
    return base + "/".join(streams)

def _stream_name_for_event(symbol: str, event: str) -> str:
    s = symbol.lower()
    if event == "aggTrade":
        return f"{s}@aggTrade"
    if event == "bookTicker":
        return f"{s}@bookTicker"
    if event == "depthUpdate":
        return f"{s}@depth20@100ms"
    return f"{s}@{event or 'unknown'}"


async def record_shadow(
    symbols: tuple[str, ...],
    *,
    db_path: str | Path,
    duration_seconds: float,
) -> dict[str, Any]:
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive")

    store = ShadowStore(db_path)
    acc = {symbol: ShadowAccumulator(symbol) for symbol in symbols}
    deadline = time.monotonic() + duration_seconds
    messages = 0
    event_counts: dict[str, int] = {}
    stream_counts: dict[str, int] = {}
    connection_errors: dict[str, str] = {}
    last_agg_id: dict[str, int] = {}

    def handle(data: dict[str, Any], stream_name: str) -> None:
        nonlocal messages
        symbol = str(data.get("s", "")).upper()
        if symbol not in acc:
            return
        event = str(data.get("e") or "unknown")

        if event == "aggTrade":
            agg_id = int(data.get("a", -1))
            if agg_id >= 0 and last_agg_id.get(symbol) == agg_id:
                return
            if agg_id >= 0:
                last_agg_id[symbol] = agg_id

        event_counts[event] = event_counts.get(event, 0) + 1
        stream_counts[stream_name] = stream_counts.get(stream_name, 0) + 1
        event_ms = int(data.get("E") or data.get("T") or int(time.time() * 1000))
        row = None

        if event == "aggTrade":
            row = acc[symbol].on_agg_trade(
                event_ms=event_ms,
                price=float(data["p"]),
                qty=float(data["q"]),
                buyer_is_maker=bool(data.get("m", False)),
            )
        elif event == "bookTicker" or (
            "b" in data and "a" in data and "B" in data and "A" in data
        ):
            row = acc[symbol].on_book_ticker(
                event_ms=event_ms,
                bid=float(data["b"]),
                bid_qty=float(data["B"]),
                ask=float(data["a"]),
                ask_qty=float(data["A"]),
            )
        elif event == "depthUpdate" or "bids" in data or "asks" in data:
            bids = data.get("b") or data.get("bids") or []
            asks = data.get("a") or data.get("asks") or []
            row = acc[symbol].on_depth(
                event_ms=event_ms,
                bids=bids,
                asks=asks,
            )

        store.write(row)
        messages += 1
        if messages % 1000 == 0:
            store.commit()

    async def stream_loop(name: str, url: str) -> None:
        failures = 0
        while time.monotonic() < deadline:
            try:
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=10,
                    max_size=4 * 1024 * 1024,
                ) as ws:
                    failures = 0
                    connection_errors.pop(name, None)
                    while time.monotonic() < deadline:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        try:
                            raw = await asyncio.wait_for(
                                ws.recv(),
                                timeout=min(30.0, remaining),
                            )
                        except asyncio.TimeoutError:
                            continue
                        payload = json.loads(raw)
                        stream_name = str(payload.get("stream", "unknown"))
                        data = payload.get("data", payload)
                        handle(data, stream_name)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures += 1
                connection_errors[name] = f"{type(exc).__name__}: {exc}"
                await asyncio.sleep(min(5.0, 0.25 * (2 ** min(failures, 4))))

    public_url = combined_url(PUBLIC_STREAM_BASE, public_stream_names(symbols))
    market_url = combined_url(MARKET_STREAM_BASE, market_stream_names(symbols))
    tasks = [
        asyncio.create_task(stream_loop("public", public_url), name="shadow-public"),
        asyncio.create_task(stream_loop("market", market_url), name="shadow-market"),
    ]

    try:
        await asyncio.gather(*tasks)
        for item in acc.values():
            store.write(item.snapshot())
        store.commit()
        return {
            "symbols": list(symbols),
            "duration_seconds": duration_seconds,
            "messages": messages,
            "rows": store.count(),
            "event_counts": event_counts,
            "stream_counts": stream_counts,
            "connection_errors": connection_errors,
            "endpoints": {
                "public": public_url,
                "market": market_url,
            },
            "db_path": str(db_path),
        }
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        store.close()


def _parse_symbols(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(x.strip().upper() for x in text.split(",") if x.strip()))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Record public Binance Futures microstructure without placing orders"
    )
    p.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    p.add_argument("--minutes", type=float, default=60.0)
    p.add_argument("--db", default="data/shadow/scalping-shadow.db")
    args = p.parse_args(argv)

    symbols = _parse_symbols(args.symbols)
    if not symbols:
        p.error("at least one symbol is required")
    report = asyncio.run(
        record_shadow(
            symbols,
            db_path=args.db,
            duration_seconds=args.minutes * 60.0,
        )
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
