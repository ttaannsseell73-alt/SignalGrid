from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Iterable

ACTIVE_ORDER_STATUSES = {"NEW", "PARTIALLY_FILLED", "PENDING_CANCEL"}
ACTIVE_ALGO_STATUSES = {"NEW", "WORKING", "PENDING"}


@dataclass(frozen=True, slots=True)
class StoredOrder:
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
    event_time: int
    last_trade_id: int | None


@dataclass(frozen=True, slots=True)
class StoredAlgoOrder:
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
    event_time: int


@dataclass(frozen=True, slots=True)
class StoredAccountPosition:
    symbol: str
    direction: str
    quantity: Decimal
    entry_price: Decimal
    notional_usdt: Decimal
    event_time: int


class StateStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS positions (
                symbol TEXT PRIMARY KEY,
                direction TEXT NOT NULL,
                notional_usdt REAL NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS account_positions (
                symbol TEXT PRIMARY KEY,
                direction TEXT NOT NULL,
                quantity TEXT NOT NULL,
                entry_price TEXT NOT NULL,
                notional_usdt TEXT NOT NULL,
                event_time INTEGER NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                client_order_id TEXT PRIMARY KEY,
                exchange_order_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                status TEXT NOT NULL,
                order_type TEXT NOT NULL,
                orig_qty TEXT NOT NULL,
                filled_qty TEXT NOT NULL,
                avg_price TEXT NOT NULL,
                reduce_only INTEGER NOT NULL,
                event_time INTEGER NOT NULL,
                last_trade_id INTEGER
            )
            """
        )
        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_exchange_id ON orders(exchange_order_id) WHERE exchange_order_id <> ''"
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS algo_orders (
                client_algo_id TEXT PRIMARY KEY,
                algo_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                status TEXT NOT NULL,
                algo_type TEXT NOT NULL,
                order_type TEXT NOT NULL,
                trigger_price TEXT NOT NULL,
                quantity TEXT NOT NULL,
                close_position INTEGER NOT NULL,
                reduce_only INTEGER NOT NULL,
                actual_order_id TEXT NOT NULL,
                event_time INTEGER NOT NULL
            )
            """
        )
        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_algo_orders_algo_id ON algo_orders(algo_id) WHERE algo_id <> ''"
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_events (
                event_key TEXT PRIMARY KEY,
                event_time INTEGER NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def upsert_position(self, symbol: str, direction: str, notional_usdt: float, updated_at: int) -> None:
        self.conn.execute(
            "INSERT INTO positions(symbol,direction,notional_usdt,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(symbol) DO UPDATE SET direction=excluded.direction, notional_usdt=excluded.notional_usdt, updated_at=excluded.updated_at",
            (symbol.upper(), direction, notional_usdt, updated_at),
        )
        self.conn.commit()

    def delete_position(self, symbol: str) -> None:
        symbol = symbol.upper()
        self.conn.execute("DELETE FROM positions WHERE symbol=?", (symbol,))
        self.conn.execute("DELETE FROM account_positions WHERE symbol=?", (symbol,))
        self.conn.commit()

    def list_positions(self) -> list[tuple[str, str, float, int]]:
        return list(self.conn.execute("SELECT symbol,direction,notional_usdt,updated_at FROM positions ORDER BY symbol"))

    def upsert_account_position(
        self,
        symbol: str,
        direction: str,
        quantity: Decimal | str | float,
        entry_price: Decimal | str | float,
        notional_usdt: Decimal | str | float,
        event_time: int,
    ) -> None:
        symbol = symbol.upper()
        qty = Decimal(str(quantity))
        entry = Decimal(str(entry_price))
        notional = Decimal(str(notional_usdt))
        self.conn.execute(
            """
            INSERT INTO account_positions(symbol,direction,quantity,entry_price,notional_usdt,event_time)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(symbol) DO UPDATE SET
                direction=excluded.direction,
                quantity=excluded.quantity,
                entry_price=excluded.entry_price,
                notional_usdt=excluded.notional_usdt,
                event_time=excluded.event_time
            """,
            (symbol, direction, str(qty), str(entry), str(notional), int(event_time)),
        )
        self.conn.execute(
            "INSERT INTO positions(symbol,direction,notional_usdt,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(symbol) DO UPDATE SET direction=excluded.direction, notional_usdt=excluded.notional_usdt, updated_at=excluded.updated_at",
            (symbol, direction, float(notional), int(event_time)),
        )
        self.conn.commit()

    def get_account_position(self, symbol: str) -> StoredAccountPosition | None:
        row = self.conn.execute(
            "SELECT symbol,direction,quantity,entry_price,notional_usdt,event_time FROM account_positions WHERE symbol=?",
            (symbol.upper(),),
        ).fetchone()
        return self._position_from_row(row) if row else None

    def list_account_positions(self) -> list[StoredAccountPosition]:
        rows = self.conn.execute(
            "SELECT symbol,direction,quantity,entry_price,notional_usdt,event_time FROM account_positions ORDER BY symbol"
        ).fetchall()
        return [self._position_from_row(row) for row in rows]

    def upsert_order(self, order: StoredOrder) -> None:
        self.conn.execute(
            """
            INSERT INTO orders(client_order_id,exchange_order_id,symbol,side,status,order_type,orig_qty,filled_qty,avg_price,reduce_only,event_time,last_trade_id)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(client_order_id) DO UPDATE SET
                exchange_order_id=excluded.exchange_order_id,
                symbol=excluded.symbol,
                side=excluded.side,
                status=excluded.status,
                order_type=excluded.order_type,
                orig_qty=excluded.orig_qty,
                filled_qty=excluded.filled_qty,
                avg_price=excluded.avg_price,
                reduce_only=excluded.reduce_only,
                event_time=excluded.event_time,
                last_trade_id=excluded.last_trade_id
            """,
            (
                order.client_order_id,
                order.exchange_order_id,
                order.symbol.upper(),
                order.side,
                order.status,
                order.order_type,
                str(order.orig_qty),
                str(order.filled_qty),
                str(order.avg_price),
                1 if order.reduce_only else 0,
                int(order.event_time),
                order.last_trade_id,
            ),
        )
        self.conn.commit()

    def get_order(self, client_order_id: str) -> StoredOrder | None:
        row = self.conn.execute(
            "SELECT client_order_id,exchange_order_id,symbol,side,status,order_type,orig_qty,filled_qty,avg_price,reduce_only,event_time,last_trade_id FROM orders WHERE client_order_id=?",
            (client_order_id,),
        ).fetchone()
        return self._order_from_row(row) if row else None

    def get_order_by_exchange_id(self, exchange_order_id: str) -> StoredOrder | None:
        row = self.conn.execute(
            "SELECT client_order_id,exchange_order_id,symbol,side,status,order_type,orig_qty,filled_qty,avg_price,reduce_only,event_time,last_trade_id FROM orders WHERE exchange_order_id=?",
            (str(exchange_order_id),),
        ).fetchone()
        return self._order_from_row(row) if row else None

    def list_orders(self, active_only: bool = False) -> list[StoredOrder]:
        if active_only:
            placeholders = ",".join("?" for _ in ACTIVE_ORDER_STATUSES)
            rows = self.conn.execute(
                f"SELECT client_order_id,exchange_order_id,symbol,side,status,order_type,orig_qty,filled_qty,avg_price,reduce_only,event_time,last_trade_id FROM orders WHERE status IN ({placeholders}) ORDER BY client_order_id",
                tuple(sorted(ACTIVE_ORDER_STATUSES)),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT client_order_id,exchange_order_id,symbol,side,status,order_type,orig_qty,filled_qty,avg_price,reduce_only,event_time,last_trade_id FROM orders ORDER BY client_order_id"
            ).fetchall()
        return [self._order_from_row(row) for row in rows]

    def upsert_algo_order(self, order: StoredAlgoOrder) -> None:
        self.conn.execute(
            """
            INSERT INTO algo_orders(client_algo_id,algo_id,symbol,side,status,algo_type,order_type,trigger_price,quantity,close_position,reduce_only,actual_order_id,event_time)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(client_algo_id) DO UPDATE SET
                algo_id=excluded.algo_id,
                symbol=excluded.symbol,
                side=excluded.side,
                status=excluded.status,
                algo_type=excluded.algo_type,
                order_type=excluded.order_type,
                trigger_price=excluded.trigger_price,
                quantity=excluded.quantity,
                close_position=excluded.close_position,
                reduce_only=excluded.reduce_only,
                actual_order_id=excluded.actual_order_id,
                event_time=excluded.event_time
            """,
            (
                order.client_algo_id,
                order.algo_id,
                order.symbol.upper(),
                order.side,
                order.status,
                order.algo_type,
                order.order_type,
                str(order.trigger_price),
                str(order.quantity),
                1 if order.close_position else 0,
                1 if order.reduce_only else 0,
                order.actual_order_id,
                int(order.event_time),
            ),
        )
        self.conn.commit()

    def get_algo_order(self, client_algo_id: str) -> StoredAlgoOrder | None:
        row = self.conn.execute(
            "SELECT client_algo_id,algo_id,symbol,side,status,algo_type,order_type,trigger_price,quantity,close_position,reduce_only,actual_order_id,event_time FROM algo_orders WHERE client_algo_id=?",
            (client_algo_id,),
        ).fetchone()
        return self._algo_from_row(row) if row else None

    def get_algo_order_by_algo_id(self, algo_id: str) -> StoredAlgoOrder | None:
        row = self.conn.execute(
            "SELECT client_algo_id,algo_id,symbol,side,status,algo_type,order_type,trigger_price,quantity,close_position,reduce_only,actual_order_id,event_time FROM algo_orders WHERE algo_id=?",
            (str(algo_id),),
        ).fetchone()
        return self._algo_from_row(row) if row else None

    def list_algo_orders(self, active_only: bool = False) -> list[StoredAlgoOrder]:
        if active_only:
            placeholders = ",".join("?" for _ in ACTIVE_ALGO_STATUSES)
            rows = self.conn.execute(
                f"SELECT client_algo_id,algo_id,symbol,side,status,algo_type,order_type,trigger_price,quantity,close_position,reduce_only,actual_order_id,event_time FROM algo_orders WHERE status IN ({placeholders}) ORDER BY client_algo_id",
                tuple(sorted(ACTIVE_ALGO_STATUSES)),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT client_algo_id,algo_id,symbol,side,status,algo_type,order_type,trigger_price,quantity,close_position,reduce_only,actual_order_id,event_time FROM algo_orders ORDER BY client_algo_id"
            ).fetchall()
        return [self._algo_from_row(row) for row in rows]

    def mark_event_once(self, event_key: str, event_time: int) -> bool:
        try:
            self.conn.execute(
                "INSERT INTO processed_events(event_key,event_time) VALUES(?,?)",
                (event_key, int(event_time)),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def set_runtime(self, key: str, value: str | int | bool) -> None:
        text = "1" if value is True else "0" if value is False else str(value)
        self.conn.execute(
            "INSERT INTO runtime_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, text),
        )
        self.conn.commit()

    def get_runtime(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM runtime_state WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_execution_ready(self, ready: bool) -> None:
        self.set_runtime("execution_ready", ready)

    def execution_ready(self) -> bool:
        return self.get_runtime("execution_ready", "0") == "1" and not self.halted()

    def halt(self, reason: str) -> None:
        self.set_runtime("halted", True)
        self.set_runtime("halt_reason", reason)
        self.set_runtime("execution_ready", False)

    def halted(self) -> bool:
        return self.get_runtime("halted", "0") == "1"

    def halt_reason(self) -> str | None:
        return self.get_runtime("halt_reason")

    def clear_halt(self) -> None:
        self.set_runtime("halted", False)
        self.set_runtime("halt_reason", "")
        self.set_runtime("execution_ready", False)

    def close(self) -> None:
        self.conn.close()

    @staticmethod
    def _order_from_row(row: Iterable[object]) -> StoredOrder:
        values = list(row)
        return StoredOrder(
            client_order_id=str(values[0]),
            exchange_order_id=str(values[1]),
            symbol=str(values[2]),
            side=str(values[3]),
            status=str(values[4]),
            order_type=str(values[5]),
            orig_qty=Decimal(str(values[6])),
            filled_qty=Decimal(str(values[7])),
            avg_price=Decimal(str(values[8])),
            reduce_only=bool(values[9]),
            event_time=int(values[10]),
            last_trade_id=None if values[11] is None else int(values[11]),
        )

    @staticmethod
    def _algo_from_row(row: Iterable[object]) -> StoredAlgoOrder:
        values = list(row)
        return StoredAlgoOrder(
            client_algo_id=str(values[0]),
            algo_id=str(values[1]),
            symbol=str(values[2]),
            side=str(values[3]),
            status=str(values[4]),
            algo_type=str(values[5]),
            order_type=str(values[6]),
            trigger_price=Decimal(str(values[7])),
            quantity=Decimal(str(values[8])),
            close_position=bool(values[9]),
            reduce_only=bool(values[10]),
            actual_order_id=str(values[11]),
            event_time=int(values[12]),
        )

    @staticmethod
    def _position_from_row(row: Iterable[object]) -> StoredAccountPosition:
        values = list(row)
        return StoredAccountPosition(
            symbol=str(values[0]),
            direction=str(values[1]),
            quantity=Decimal(str(values[2])),
            entry_price=Decimal(str(values[3])),
            notional_usdt=Decimal(str(values[4])),
            event_time=int(values[5]),
        )
