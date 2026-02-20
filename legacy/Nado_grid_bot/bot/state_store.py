"""SQLite-based state store for Nado Grid Bot.

Stores grid parameters, orders, pending buffers, fill logs, and bot state.
All writes are transactional; reads use WAL mode for concurrency.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("nado_grid")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BOT_STATE_RUNNING = "RUNNING"
BOT_STATE_PAUSED = "PAUSED"

ORDER_STATUS_ACTIVE = "active"
ORDER_STATUS_FILLED = "filled"
ORDER_STATUS_CANCELLED = "cancelled"
ORDER_STATUS_EXTERNAL = "external"

# ---------------------------------------------------------------------------
# Data classes for rows
# ---------------------------------------------------------------------------


@dataclass
class OrderRow:
    """Represents one row in the `orders` table."""

    order_digest: str
    k: int
    side: str  # "buy" or "sell"
    price_x18: str  # stored as TEXT to avoid int overflow
    qty_total_x18: str
    qty_remaining_x18: str
    status: str  # active / filled / cancelled / external
    created_at: float
    updated_at: float


@dataclass
class PendingBufferRow:
    """Represents one row in the `pending_buffers` table."""

    k: int
    side: str
    pending_qty_x18: str


@dataclass
class FillLogRow:
    """Represents one row in the `fills_log` table."""

    fill_id: Optional[int]
    order_digest: str
    filled_qty: str
    remaining_qty: str
    price: str
    is_taker: bool
    fee: str
    ts: float


# ---------------------------------------------------------------------------
# StateStore
# ---------------------------------------------------------------------------


class StateStore:
    """Persistent state backed by SQLite."""

    def __init__(self, db_path: str) -> None:
        import os

        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.row_factory = sqlite3.Row
        self._create_tables()

        logger.info("state_store_opened", extra={"db_path": db_path})

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _create_tables(self) -> None:
        with self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS grid_params (
                    id          INTEGER PRIMARY KEY CHECK (id = 1),
                    p0_x18      TEXT    NOT NULL,
                    grid_step_pct REAL  NOT NULL,
                    levels_down INTEGER NOT NULL,
                    levels_up   INTEGER NOT NULL,
                    created_at  REAL    NOT NULL
                );

                CREATE TABLE IF NOT EXISTS orders (
                    order_digest     TEXT PRIMARY KEY,
                    k                INTEGER NOT NULL,
                    side             TEXT    NOT NULL,
                    price_x18        TEXT    NOT NULL,
                    qty_total_x18    TEXT    NOT NULL,
                    qty_remaining_x18 TEXT   NOT NULL,
                    status           TEXT    NOT NULL DEFAULT 'active',
                    created_at       REAL    NOT NULL,
                    updated_at       REAL    NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_orders_k_side
                    ON orders (k, side)
                    WHERE status = 'active';

                CREATE TABLE IF NOT EXISTS pending_buffers (
                    k               INTEGER NOT NULL,
                    side            TEXT    NOT NULL,
                    pending_qty_x18 TEXT    NOT NULL DEFAULT '0',
                    PRIMARY KEY (k, side)
                );

                CREATE TABLE IF NOT EXISTS fills_log (
                    fill_id       INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_digest  TEXT    NOT NULL,
                    filled_qty    TEXT    NOT NULL,
                    remaining_qty TEXT    NOT NULL,
                    price         TEXT    NOT NULL,
                    is_taker      INTEGER NOT NULL DEFAULT 0,
                    fee           TEXT    NOT NULL DEFAULT '0',
                    ts            REAL    NOT NULL
                );

                CREATE TABLE IF NOT EXISTS bot_state (
                    id    INTEGER PRIMARY KEY CHECK (id = 1),
                    state TEXT NOT NULL DEFAULT 'RUNNING'
                );

                INSERT OR IGNORE INTO bot_state (id, state)
                    VALUES (1, 'RUNNING');
            """)

    # ------------------------------------------------------------------
    # Grid params
    # ------------------------------------------------------------------

    def save_grid_params(
        self,
        p0_x18: int,
        grid_step_pct: float,
        levels_down: int,
        levels_up: int,
    ) -> None:
        now = time.time()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO grid_params (id, p0_x18, grid_step_pct, levels_down, levels_up, created_at)
                VALUES (1, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    p0_x18 = excluded.p0_x18,
                    grid_step_pct = excluded.grid_step_pct,
                    levels_down = excluded.levels_down,
                    levels_up = excluded.levels_up,
                    created_at = excluded.created_at
                """,
                (str(p0_x18), grid_step_pct, levels_down, levels_up, now),
            )

    def get_grid_params(self) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT p0_x18, grid_step_pct, levels_down, levels_up, created_at FROM grid_params WHERE id = 1"
        ).fetchone()
        if row is None:
            return None
        return dict(row)

    # ------------------------------------------------------------------
    # Bot state
    # ------------------------------------------------------------------

    def get_bot_state(self) -> str:
        row = self._conn.execute("SELECT state FROM bot_state WHERE id = 1").fetchone()
        return row["state"] if row else BOT_STATE_RUNNING

    def set_bot_state(self, state: str) -> None:
        with self._conn:
            self._conn.execute("UPDATE bot_state SET state = ? WHERE id = 1", (state,))
        logger.info("bot_state_changed", extra={"bot_state": state})

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def upsert_order(
        self,
        order_digest: str,
        k: int,
        side: str,
        price_x18: int,
        qty_total_x18: int,
        qty_remaining_x18: int,
        status: str = ORDER_STATUS_ACTIVE,
    ) -> None:
        now = time.time()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO orders
                    (order_digest, k, side, price_x18, qty_total_x18,
                     qty_remaining_x18, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(order_digest) DO UPDATE SET
                    qty_remaining_x18 = excluded.qty_remaining_x18,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (
                    order_digest,
                    k,
                    side,
                    str(price_x18),
                    str(qty_total_x18),
                    str(qty_remaining_x18),
                    status,
                    now,
                    now,
                ),
            )

    def get_order(self, order_digest: str) -> Optional[OrderRow]:
        row = self._conn.execute(
            "SELECT * FROM orders WHERE order_digest = ?", (order_digest,)
        ).fetchone()
        if row is None:
            return None
        return OrderRow(**dict(row))

    def get_active_orders(self) -> list[OrderRow]:
        rows = self._conn.execute(
            "SELECT * FROM orders WHERE status = 'active' ORDER BY k"
        ).fetchall()
        return [OrderRow(**dict(r)) for r in rows]

    def get_all_orders(self) -> list[OrderRow]:
        rows = self._conn.execute("SELECT * FROM orders ORDER BY k").fetchall()
        return [OrderRow(**dict(r)) for r in rows]

    def get_active_order_at_level(self, k: int, side: str) -> Optional[OrderRow]:
        """Return the active order for a given level and side (if any)."""
        row = self._conn.execute(
            "SELECT * FROM orders WHERE k = ? AND side = ? AND status = 'active'",
            (k, side),
        ).fetchone()
        if row is None:
            return None
        return OrderRow(**dict(row))

    def update_order_remaining(self, order_digest: str, qty_remaining_x18: int) -> None:
        now = time.time()
        with self._conn:
            self._conn.execute(
                """
                UPDATE orders SET qty_remaining_x18 = ?, updated_at = ?
                WHERE order_digest = ?
                """,
                (str(qty_remaining_x18), now, order_digest),
            )

    def update_order_status(self, order_digest: str, status: str) -> None:
        now = time.time()
        with self._conn:
            self._conn.execute(
                "UPDATE orders SET status = ?, updated_at = ? WHERE order_digest = ?",
                (status, now, order_digest),
            )

    def count_active_orders(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM orders WHERE status = 'active'"
        ).fetchone()
        return row["cnt"]

    # ------------------------------------------------------------------
    # Pending buffers
    # ------------------------------------------------------------------

    def get_pending_buffer(self, k: int, side: str) -> int:
        row = self._conn.execute(
            "SELECT pending_qty_x18 FROM pending_buffers WHERE k = ? AND side = ?",
            (k, side),
        ).fetchone()
        if row is None:
            return 0
        return int(row["pending_qty_x18"])

    def set_pending_buffer(self, k: int, side: str, qty_x18: int) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO pending_buffers (k, side, pending_qty_x18)
                VALUES (?, ?, ?)
                ON CONFLICT(k, side) DO UPDATE SET
                    pending_qty_x18 = excluded.pending_qty_x18
                """,
                (k, side, str(qty_x18)),
            )

    def get_all_pending_buffers(self) -> list[PendingBufferRow]:
        rows = self._conn.execute(
            "SELECT k, side, pending_qty_x18 FROM pending_buffers WHERE CAST(pending_qty_x18 AS INTEGER) > 0"
        ).fetchall()
        return [PendingBufferRow(**dict(r)) for r in rows]

    # ------------------------------------------------------------------
    # Fills log
    # ------------------------------------------------------------------

    def log_fill(
        self,
        order_digest: str,
        filled_qty: int,
        remaining_qty: int,
        price: int,
        is_taker: bool,
        fee: int,
    ) -> None:
        now = time.time()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO fills_log
                    (order_digest, filled_qty, remaining_qty, price, is_taker, fee, ts)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_digest,
                    str(filled_qty),
                    str(remaining_qty),
                    str(price),
                    int(is_taker),
                    str(fee),
                    now,
                ),
            )

    def get_fills_for_order(self, order_digest: str) -> list[FillLogRow]:
        rows = self._conn.execute(
            "SELECT * FROM fills_log WHERE order_digest = ? ORDER BY ts",
            (order_digest,),
        ).fetchall()
        return [
            FillLogRow(
                fill_id=r["fill_id"],
                order_digest=r["order_digest"],
                filled_qty=r["filled_qty"],
                remaining_qty=r["remaining_qty"],
                price=r["price"],
                is_taker=bool(r["is_taker"]),
                fee=r["fee"],
                ts=r["ts"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._conn.close()
        logger.info("state_store_closed")
