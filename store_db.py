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
        # Audit trail. Every stock change lands here, so "who took what, when"
        # is answerable after the fact — a ward store needs that even in a POC.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS movements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                barcode TEXT NOT NULL,
                delta INTEGER NOT NULL,
                kind TEXT NOT NULL,
                note TEXT,
                at TEXT NOT NULL,
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


def product_batches(barcode):
    """A product's batches, soonest-expiry first (undated last)."""
    conn = _connect()
    rows = conn.execute(
        """
        SELECT * FROM batches
        WHERE barcode = ?
        ORDER BY (expiry IS NULL OR expiry = ''), expiry ASC, id ASC
        """,
        (barcode,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def on_hand(barcode):
    conn = _connect()
    row = conn.execute(
        "SELECT COALESCE(SUM(quantity), 0) AS n FROM batches WHERE barcode = ?",
        (barcode,),
    ).fetchone()
    conn.close()
    return int(row["n"])


# --------------------------------------------------------------------------
# Admin: product management
# --------------------------------------------------------------------------
def update_product(barcode, name, min_stock):
    conn = _connect()
    with conn:
        conn.execute(
            "UPDATE products SET name = ?, min_stock = ? WHERE barcode = ?",
            (name, int(min_stock), barcode),
        )
    conn.close()


def delete_product(barcode):
    """Remove a product and all of its stock. Irreversible."""
    conn = _connect()
    with conn:
        conn.execute("DELETE FROM batches WHERE barcode = ?", (barcode,))
        conn.execute("DELETE FROM products WHERE barcode = ?", (barcode,))
    conn.close()


# Unambiguous alphabet. These codes get printed on labels and read back by eye
# when a scan fails, so one glyph of each look-alike pair is dropped:
#   0/O  1/I/L  2/Z  5/S  6/G  8/B  9/Q  U/V
# 22 glyphs still gives 22^6 ≈ 113M codes — far more than a ward store needs.
_CODE_ALPHABET = "ACDEFHJKMNPRTVWXY34679"
INTERNAL_PREFIX = "WARD"


def next_internal_barcode(rng=None):
    """Mint an internal code for an item that has no manufacturer barcode.

    Format: WARD-XXXXXX. Code128 encodes this directly, so the generated label
    scans with the same camera flow as a retail barcode.
    """
    import random

    rng = rng or random.SystemRandom()
    conn = _connect()
    try:
        for _ in range(50):
            body = "".join(rng.choice(_CODE_ALPHABET) for _ in range(6))
            code = f"{INTERNAL_PREFIX}-{body}"
            hit = conn.execute(
                "SELECT 1 FROM products WHERE barcode = ?", (code,)
            ).fetchone()
            if not hit:
                return code
    finally:
        conn.close()
    raise RuntimeError("Could not mint a unique internal barcode after 50 tries")


def is_internal(barcode):
    return bool(barcode) and barcode.startswith(INTERNAL_PREFIX + "-")


# --------------------------------------------------------------------------
# Movements: consume / add back
# --------------------------------------------------------------------------
def log_movement(conn, barcode, delta, kind, note=None):
    conn.execute(
        "INSERT INTO movements (barcode, delta, kind, note, at) VALUES (?, ?, ?, ?, ?)",
        (barcode, int(delta), kind, note, _now()),
    )


def log_movement_standalone(barcode, delta, kind, note=None):
    """Log a movement on its own connection, for callers outside a transaction
    (e.g. receiving stock via add_batch)."""
    conn = _connect()
    try:
        with conn:
            log_movement(conn, barcode, delta, kind, note)
    finally:
        conn.close()


def consume(barcode, quantity, note=None):
    """Deplete `quantity` units, first-expiry-first-out.

    Returns (consumed, shortfall). Consumes as much as is on hand and reports
    the shortfall rather than refusing outright or letting stock go negative —
    a partial withdrawal is real and the count should reflect it.
    """
    want = int(quantity)
    if want <= 0:
        return 0, 0

    conn = _connect()
    try:
        with conn:
            rows = conn.execute(
                """
                SELECT id, quantity FROM batches
                WHERE barcode = ? AND quantity > 0
                ORDER BY (expiry IS NULL OR expiry = ''), expiry ASC, id ASC
                """,
                (barcode,),
            ).fetchall()

            remaining = want
            for r in rows:
                if remaining <= 0:
                    break
                take = min(int(r["quantity"]), remaining)
                conn.execute(
                    "UPDATE batches SET quantity = quantity - ? WHERE id = ?",
                    (take, r["id"]),
                )
                remaining -= take

            consumed = want - remaining
            if consumed:
                log_movement(conn, barcode, -consumed, "consume", note)
        return consumed, remaining
    finally:
        conn.close()


def add_back(barcode, quantity, note=None):
    """Return `quantity` units to stock.

    Rejoins the soonest-expiring existing batch when there is one, so returned
    stock keeps its original expiry rather than becoming undated. Falls back to
    a fresh undated batch if the product has no batches left.
    """
    qty = int(quantity)
    if qty <= 0:
        return 0

    conn = _connect()
    try:
        with conn:
            row = conn.execute(
                """
                SELECT id FROM batches
                WHERE barcode = ?
                ORDER BY (expiry IS NULL OR expiry = ''), expiry ASC, id ASC
                LIMIT 1
                """,
                (barcode,),
            ).fetchone()

            if row:
                conn.execute(
                    "UPDATE batches SET quantity = quantity + ? WHERE id = ?",
                    (qty, row["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO batches (barcode, quantity, expiry, lot, added_at) "
                    "VALUES (?, ?, NULL, NULL, ?)",
                    (barcode, qty, _now()),
                )
            log_movement(conn, barcode, qty, "add_back", note)
        return qty
    finally:
        conn.close()


def recent_movements(limit=50):
    conn = _connect()
    rows = conn.execute(
        """
        SELECT m.id, m.barcode, COALESCE(p.name, m.barcode) AS name,
               m.delta, m.kind, m.note, m.at
        FROM movements m LEFT JOIN products p ON p.barcode = m.barcode
        ORDER BY m.id DESC LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
