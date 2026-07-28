"""Persistent store inventory (SQLite).

Model:
  products : one row per product, keyed by barcode (GTIN or raw code).
  batches  : stock added over time; each batch has a quantity and (optionally)
             an expiry + lot. A product's on-hand count is the sum of its
             batches' quantities. Expiry is per-batch because the same product
             can hold multiple lots expiring on different dates.

Separate DB file (store.db) from the vision POC's inventory.db.
"""

import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "store.db")


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _connect()
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                barcode TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                min_stock INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                barcode TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 0,
                expiry TEXT,
                lot TEXT,
                added_at TEXT NOT NULL,
                FOREIGN KEY (barcode) REFERENCES products(barcode)
            )
            """
        )
    conn.close()


def _now():
    return datetime.now().isoformat(timespec="seconds")


def get_product(barcode):
    conn = _connect()
    row = conn.execute(
        "SELECT * FROM products WHERE barcode = ?", (barcode,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def register_product(barcode, name, min_stock=0):
    init_db()
    conn = _connect()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO products (barcode, name, min_stock, created_at) "
            "VALUES (?, ?, ?, COALESCE((SELECT created_at FROM products WHERE barcode = ?), ?))",
            (barcode, name, int(min_stock), barcode, _now()),
        )
    conn.close()


def update_min_stock(barcode, min_stock):
    conn = _connect()
    with conn:
        conn.execute(
            "UPDATE products SET min_stock = ? WHERE barcode = ?",
            (int(min_stock), barcode),
        )
    conn.close()


def add_batch(barcode, quantity, expiry=None, lot=None):
    """Add stock. expiry is an ISO date string or None."""
    init_db()
    conn = _connect()
    with conn:
        conn.execute(
            "INSERT INTO batches (barcode, quantity, expiry, lot, added_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (barcode, int(quantity), expiry, lot, _now()),
        )
    conn.close()


def set_batch_quantity(batch_id, quantity):
    conn = _connect()
    with conn:
        conn.execute(
            "UPDATE batches SET quantity = ? WHERE id = ?", (int(quantity), batch_id)
        )
    conn.close()


def delete_batch(batch_id):
    conn = _connect()
    with conn:
        conn.execute("DELETE FROM batches WHERE id = ?", (batch_id,))
    conn.close()


def inventory_summary():
    """Per product: name, on-hand quantity, min_stock, below_min."""
    conn = _connect()
    rows = conn.execute(
        """
        SELECT p.barcode, p.name, p.min_stock,
               COALESCE(SUM(b.quantity), 0) AS on_hand
        FROM products p
        LEFT JOIN batches b ON b.barcode = p.barcode
        GROUP BY p.barcode, p.name, p.min_stock
        ORDER BY p.name
        """
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["below_min"] = d["on_hand"] < d["min_stock"]
        out.append(d)
    return out


def batches_with_expiry():
    """All batches that have an expiry, with product name, for the expiry view."""
    conn = _connect()
    rows = conn.execute(
        """
        SELECT b.id, b.barcode, p.name, b.quantity, b.expiry, b.lot
        FROM batches b JOIN products p ON p.barcode = b.barcode
        WHERE b.expiry IS NOT NULL AND b.expiry != ''
        ORDER BY b.expiry ASC
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def all_batches():
    conn = _connect()
    rows = conn.execute(
        """
        SELECT b.id, b.barcode, p.name, b.quantity, b.expiry, b.lot, b.added_at
        FROM batches b JOIN products p ON p.barcode = b.barcode
        ORDER BY b.added_at DESC
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_products():
    conn = _connect()
    rows = conn.execute("SELECT * FROM products ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]
