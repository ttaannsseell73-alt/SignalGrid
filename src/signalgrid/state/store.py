from __future__ import annotations
import sqlite3
from pathlib import Path

class StateStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            symbol TEXT PRIMARY KEY,
            direction TEXT NOT NULL,
            notional_usdt REAL NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """)
        self.conn.commit()

    def upsert_position(self, symbol: str, direction: str, notional_usdt: float, updated_at: int) -> None:
        self.conn.execute(
            "INSERT INTO positions(symbol,direction,notional_usdt,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(symbol) DO UPDATE SET direction=excluded.direction, notional_usdt=excluded.notional_usdt, updated_at=excluded.updated_at",
            (symbol, direction, notional_usdt, updated_at),
        )
        self.conn.commit()

    def delete_position(self, symbol: str) -> None:
        self.conn.execute("DELETE FROM positions WHERE symbol=?", (symbol,))
        self.conn.commit()

    def list_positions(self) -> list[tuple[str, str, float, int]]:
        return list(self.conn.execute("SELECT symbol,direction,notional_usdt,updated_at FROM positions ORDER BY symbol"))
