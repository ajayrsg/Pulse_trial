"""Data layer for the multi-storeroom inventory app.

Model
-----
agency            an organisation; storerooms transfer stock only within one
storeroom         a physical store, optionally with a webhook URL
app_user          a person, with one of three roles (see ROLES)
user_storeroom    which storerooms a person may operate
item              the MASTER inventory list: name, barcode, UOM
storeroom_item    which items a storeroom is supposed to carry, plus min_qty
batch             actual stock: (storeroom, item, expiry) -> quantity
movement          an append-only audit row for every stock change

Batches are keyed on (storeroom, item, expiry) so stocking up an expiry that
is already on the shelf adds to that count instead of creating a second row.

Withdrawals, transfers and disposals consume first-expiry-first-out and write
one movement row PER batch touched, so the activity log can state exactly which
expiry date left the shelf.
"""

import os
import sqlite3
from datetime import date, datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ward.db")

# --------------------------------------------------------------------------
# Roles
# --------------------------------------------------------------------------
ROLE_APP_ADMIN = "app_admin"
ROLE_TEAM_ADMIN = "team_admin"
ROLE_USER = "user"

ROLES = {
    ROLE_APP_ADMIN: "App Admin",
    ROLE_TEAM_ADMIN: "Team Admin",
    ROLE_USER: "User",
}

# Day-to-day capabilities. Team Admin is a User plus dispose/transfer.
_USER_CAPS = {
    "add_stock", "withdraw", "activity", "inventory", "low_stock", "expiry",
}
_TEAM_ADMIN_CAPS = _USER_CAPS | {"dispose", "transfer"}
_APP_ADMIN_CAPS = _TEAM_ADMIN_CAPS | {
    "manage_items", "manage_storerooms", "manage_users", "manage_webhooks",
}

CAPS = {
    ROLE_USER: _USER_CAPS,
    ROLE_TEAM_ADMIN: _TEAM_ADMIN_CAPS,
    ROLE_APP_ADMIN: _APP_ADMIN_CAPS,
}

# Movement kinds
K_STOCK_IN = "stock_in"
K_WITHDRAW = "withdraw"
K_DISPOSE = "dispose"
K_TRANSFER_OUT = "transfer_out"
K_TRANSFER_IN = "transfer_in"
K_ADJUST = "adjust"

KIND_LABELS = {
    K_STOCK_IN: "Stock in",
    K_WITHDRAW: "Withdrawn",
    K_DISPOSE: "Disposed",
    K_TRANSFER_OUT: "Transfer out",
    K_TRANSFER_IN: "Transfer in",
    K_ADJUST: "Adjustment",
}


def can(role, capability):
    return capability in CAPS.get(role, set())


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _now():
    return datetime.now().isoformat(timespec="seconds")


def init_db():
    conn = _connect()
    with conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS agency (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS storeroom (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agency_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                webhook_url TEXT,
                created_at TEXT NOT NULL,
                UNIQUE (agency_id, name),
                FOREIGN KEY (agency_id) REFERENCES agency(id)
            );

            CREATE TABLE IF NOT EXISTS app_user (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agency_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                email TEXT,
                role TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (agency_id) REFERENCES agency(id)
            );

            CREATE TABLE IF NOT EXISTS user_storeroom (
                user_id INTEGER NOT NULL,
                storeroom_id INTEGER NOT NULL,
                PRIMARY KEY (user_id, storeroom_id),
                FOREIGN KEY (user_id) REFERENCES app_user(id) ON DELETE CASCADE,
                FOREIGN KEY (storeroom_id) REFERENCES storeroom(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS item (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agency_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                barcode TEXT,
                uom TEXT NOT NULL DEFAULT 'EA',
                created_at TEXT NOT NULL,
                UNIQUE (agency_id, barcode),
                FOREIGN KEY (agency_id) REFERENCES agency(id)
            );

            CREATE TABLE IF NOT EXISTS storeroom_item (
                storeroom_id INTEGER NOT NULL,
                item_id INTEGER NOT NULL,
                min_qty INTEGER,
                PRIMARY KEY (storeroom_id, item_id),
                FOREIGN KEY (storeroom_id) REFERENCES storeroom(id) ON DELETE CASCADE,
                FOREIGN KEY (item_id) REFERENCES item(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS batch (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                storeroom_id INTEGER NOT NULL,
                item_id INTEGER NOT NULL,
                expiry TEXT,
                quantity INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (storeroom_id) REFERENCES storeroom(id) ON DELETE CASCADE,
                FOREIGN KEY (item_id) REFERENCES item(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS movement (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                storeroom_id INTEGER NOT NULL,
                item_id INTEGER NOT NULL,
                delta INTEGER NOT NULL,
                kind TEXT NOT NULL,
                expiry TEXT,
                reason TEXT,
                counterpart_storeroom_id INTEGER,
                user_id INTEGER,
                at TEXT NOT NULL,
                FOREIGN KEY (storeroom_id) REFERENCES storeroom(id) ON DELETE CASCADE,
                FOREIGN KEY (item_id) REFERENCES item(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_batch_lookup
                ON batch (storeroom_id, item_id, expiry);
            CREATE INDEX IF NOT EXISTS idx_movement_room
                ON movement (storeroom_id, id DESC);
            """
        )
        # A single expiry per (storeroom, item) row, so stocking an expiry that
        # already exists adds to it. NULL expiry is its own bucket; SQLite
        # treats NULLs as distinct in a UNIQUE index, so undated stock is
        # consolidated explicitly in stock_in() instead.
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_batch_unique "
            "ON batch (storeroom_id, item_id, expiry)"
        )
    conn.close()


# --------------------------------------------------------------------------
# Agency
# --------------------------------------------------------------------------
def ensure_agency(name="My Agency"):
    """Return the agency id, creating it on first run."""
    init_db()
    conn = _connect()
    try:
        with conn:
            row = conn.execute("SELECT id FROM agency LIMIT 1").fetchone()
            if row:
                return row["id"]
            cur = conn.execute("INSERT INTO agency (name) VALUES (?)", (name,))
            return cur.lastrowid
    finally:
        conn.close()


def get_agency(agency_id):
    conn = _connect()
    row = conn.execute("SELECT * FROM agency WHERE id = ?", (agency_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def rename_agency(agency_id, name):
    conn = _connect()
    with conn:
        conn.execute("UPDATE agency SET name = ? WHERE id = ?", (name, agency_id))
    conn.close()


# --------------------------------------------------------------------------
# Storerooms
# --------------------------------------------------------------------------
def create_storeroom(agency_id, name, webhook_url=None):
    conn = _connect()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO storeroom (agency_id, name, webhook_url, created_at) "
                "VALUES (?, ?, ?, ?)",
                (agency_id, name.strip(), (webhook_url or "").strip() or None, _now()),
            )
            return cur.lastrowid
    finally:
        conn.close()


def list_storerooms(agency_id):
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM storeroom WHERE agency_id = ? ORDER BY name", (agency_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_storeroom(storeroom_id):
    conn = _connect()
    row = conn.execute("SELECT * FROM storeroom WHERE id = ?", (storeroom_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_storeroom(storeroom_id, name=None, webhook_url=None):
    conn = _connect()
    with conn:
        if name is not None:
            conn.execute("UPDATE storeroom SET name = ? WHERE id = ?", (name.strip(), storeroom_id))
        if webhook_url is not None:
            conn.execute(
                "UPDATE storeroom SET webhook_url = ? WHERE id = ?",
                (webhook_url.strip() or None, storeroom_id),
            )
    conn.close()


def delete_storeroom(storeroom_id):
    conn = _connect()
    with conn:
        conn.execute("DELETE FROM storeroom WHERE id = ?", (storeroom_id,))
    conn.close()


def search_storerooms(agency_id, term, exclude_id=None, limit=20):
    """Typeahead for the transfer destination picker."""
    like = f"%{(term or '').strip()}%"
    sql = "SELECT * FROM storeroom WHERE agency_id = ? AND name LIKE ?"
    args = [agency_id, like]
    if exclude_id:
        sql += " AND id != ?"
        args.append(exclude_id)
    sql += " ORDER BY name LIMIT ?"
    args.append(int(limit))
    conn = _connect()
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------
def create_user(agency_id, name, role=ROLE_USER, email=None):
    conn = _connect()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO app_user (agency_id, name, email, role, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (agency_id, name.strip(), (email or "").strip() or None, role, _now()),
            )
            return cur.lastrowid
    finally:
        conn.close()


def list_users(agency_id):
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM app_user WHERE agency_id = ? ORDER BY name", (agency_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_user(user_id):
    conn = _connect()
    row = conn.execute("SELECT * FROM app_user WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_user(user_id, name=None, role=None, email=None):
    conn = _connect()
    with conn:
        if name is not None:
            conn.execute("UPDATE app_user SET name = ? WHERE id = ?", (name.strip(), user_id))
        if role is not None:
            conn.execute("UPDATE app_user SET role = ? WHERE id = ?", (role, user_id))
        if email is not None:
            conn.execute(
                "UPDATE app_user SET email = ? WHERE id = ?",
                ((email or "").strip() or None, user_id),
            )
    conn.close()


def delete_user(user_id):
    conn = _connect()
    with conn:
        conn.execute("DELETE FROM app_user WHERE id = ?", (user_id,))
    conn.close()


def assign_user(user_id, storeroom_id):
    conn = _connect()
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO user_storeroom (user_id, storeroom_id) VALUES (?, ?)",
            (user_id, storeroom_id),
        )
    conn.close()


def unassign_user(user_id, storeroom_id):
    conn = _connect()
    with conn:
        conn.execute(
            "DELETE FROM user_storeroom WHERE user_id = ? AND storeroom_id = ?",
            (user_id, storeroom_id),
        )
    conn.close()


def users_in_storeroom(storeroom_id):
    conn = _connect()
    rows = conn.execute(
        """
        SELECT u.* FROM app_user u
        JOIN user_storeroom us ON us.user_id = u.id
        WHERE us.storeroom_id = ?
        ORDER BY u.name
        """,
        (storeroom_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def storerooms_for_user(user_id):
    conn = _connect()
    rows = conn.execute(
        """
        SELECT s.* FROM storeroom s
        JOIN user_storeroom us ON us.storeroom_id = s.id
        WHERE us.user_id = ?
        ORDER BY s.name
        """,
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# Master item list
# --------------------------------------------------------------------------
def create_item(agency_id, name, barcode=None, uom="EA"):
    conn = _connect()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO item (agency_id, name, barcode, uom, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (agency_id, name.strip(), (barcode or "").strip() or None,
                 (uom or "EA").upper(), _now()),
            )
            return cur.lastrowid
    finally:
        conn.close()


def list_items(agency_id):
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM item WHERE agency_id = ? ORDER BY name", (agency_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_item(item_id):
    conn = _connect()
    row = conn.execute("SELECT * FROM item WHERE id = ?", (item_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def item_by_barcode(agency_id, barcode):
    conn = _connect()
    row = conn.execute(
        "SELECT * FROM item WHERE agency_id = ? AND barcode = ?", (agency_id, barcode)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_item(item_id, name=None, barcode=None, uom=None):
    conn = _connect()
    with conn:
        if name is not None:
            conn.execute("UPDATE item SET name = ? WHERE id = ?", (name.strip(), item_id))
        if barcode is not None:
            conn.execute(
                "UPDATE item SET barcode = ? WHERE id = ?",
                ((barcode or "").strip() or None, item_id),
            )
        if uom is not None:
            conn.execute("UPDATE item SET uom = ? WHERE id = ?", (uom.upper(), item_id))
    conn.close()


def delete_item(item_id):
    conn = _connect()
    with conn:
        conn.execute("DELETE FROM item WHERE id = ?", (item_id,))
    conn.close()


def item_total_on_hand(item_id):
    """Across every storeroom — used to block deleting an item that exists."""
    conn = _connect()
    row = conn.execute(
        "SELECT COALESCE(SUM(quantity), 0) AS n FROM batch WHERE item_id = ?", (item_id,)
    ).fetchone()
    conn.close()
    return int(row["n"])


# --------------------------------------------------------------------------
# Storeroom <-> item assignment
# --------------------------------------------------------------------------
def assign_item(storeroom_id, item_id, min_qty=None, opening_qty=0,
                expiry=None, user_id=None):
    """Tag an item to a storeroom. Optionally seed an opening quantity."""
    conn = _connect()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO storeroom_item (storeroom_id, item_id, min_qty) "
            "VALUES (?, ?, ?)",
            (storeroom_id, item_id, None if min_qty in (None, "") else int(min_qty)),
        )
    conn.close()
    if opening_qty and int(opening_qty) > 0:
        stock_in(storeroom_id, item_id, int(opening_qty), expiry, user_id=user_id)


def set_min_qty(storeroom_id, item_id, min_qty):
    conn = _connect()
    with conn:
        conn.execute(
            "UPDATE storeroom_item SET min_qty = ? WHERE storeroom_id = ? AND item_id = ?",
            (None if min_qty in (None, "") else int(min_qty), storeroom_id, item_id),
        )
    conn.close()


def on_hand(storeroom_id, item_id):
    conn = _connect()
    row = conn.execute(
        "SELECT COALESCE(SUM(quantity), 0) AS n FROM batch "
        "WHERE storeroom_id = ? AND item_id = ?",
        (storeroom_id, item_id),
    ).fetchone()
    conn.close()
    return int(row["n"])


def unassign_item(storeroom_id, item_id):
    """Remove an item from a storeroom.

    Refuses while stock remains: an assignment is what makes the count
    meaningful, so dropping it under a non-zero count would orphan real stock.
    Returns (ok, message).
    """
    qty = on_hand(storeroom_id, item_id)
    if qty > 0:
        return False, f"{qty} still on hand — withdraw, transfer or dispose of it first."
    conn = _connect()
    with conn:
        conn.execute(
            "DELETE FROM storeroom_item WHERE storeroom_id = ? AND item_id = ?",
            (storeroom_id, item_id),
        )
        conn.execute(
            "DELETE FROM batch WHERE storeroom_id = ? AND item_id = ? AND quantity = 0",
            (storeroom_id, item_id),
        )
    conn.close()
    return True, "Unassigned."


def storeroom_items(storeroom_id):
    """Assigned items with on-hand totals and min_qty."""
    conn = _connect()
    rows = conn.execute(
        """
        SELECT i.id AS item_id, i.name, i.barcode, i.uom,
               si.min_qty,
               COALESCE((SELECT SUM(b.quantity) FROM batch b
                         WHERE b.storeroom_id = si.storeroom_id
                           AND b.item_id = i.id), 0) AS on_hand
        FROM storeroom_item si
        JOIN item i ON i.id = si.item_id
        WHERE si.storeroom_id = ?
        ORDER BY i.name
        """,
        (storeroom_id,),
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["below_min"] = d["min_qty"] is not None and d["on_hand"] < d["min_qty"]
        out.append(d)
    return out


def is_assigned(storeroom_id, item_id):
    conn = _connect()
    row = conn.execute(
        "SELECT 1 FROM storeroom_item WHERE storeroom_id = ? AND item_id = ?",
        (storeroom_id, item_id),
    ).fetchone()
    conn.close()
    return row is not None


def low_stock(storeroom_id):
    return [r for r in storeroom_items(storeroom_id) if r["below_min"]]


def batches(storeroom_id, item_id=None, with_zero=False):
    """Batches soonest-expiry-first; undated last."""
    sql = """
        SELECT b.*, i.name, i.uom
        FROM batch b JOIN item i ON i.id = b.item_id
        WHERE b.storeroom_id = ?
    """
    args = [storeroom_id]
    if item_id is not None:
        sql += " AND b.item_id = ?"
        args.append(item_id)
    if not with_zero:
        sql += " AND b.quantity > 0"
    sql += " ORDER BY (b.expiry IS NULL OR b.expiry = ''), b.expiry ASC, b.id ASC"
    conn = _connect()
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def expiring(storeroom_id, within_days=None):
    """Dated batches with stock, soonest first. within_days filters the horizon."""
    out = []
    today = date.today()
    for b in batches(storeroom_id):
        if not b["expiry"]:
            continue
        try:
            d = datetime.strptime(b["expiry"], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        days = (d - today).days
        if within_days is not None and days > within_days:
            continue
        b = dict(b)
        b["days_left"] = days
        out.append(b)
    return out


# --------------------------------------------------------------------------
# Stock movements
# --------------------------------------------------------------------------
def _log(conn, storeroom_id, item_id, delta, kind, expiry=None, reason=None,
         counterpart=None, user_id=None):
    conn.execute(
        "INSERT INTO movement (storeroom_id, item_id, delta, kind, expiry, reason, "
        "counterpart_storeroom_id, user_id, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (storeroom_id, item_id, int(delta), kind, expiry, reason,
         counterpart, user_id, _now()),
    )


def _add_to_batch(conn, storeroom_id, item_id, qty, expiry):
    """Add qty to the (storeroom, item, expiry) batch, creating it if needed."""
    expiry = expiry or None
    if expiry is None:
        row = conn.execute(
            "SELECT id FROM batch WHERE storeroom_id = ? AND item_id = ? AND expiry IS NULL",
            (storeroom_id, item_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id FROM batch WHERE storeroom_id = ? AND item_id = ? AND expiry = ?",
            (storeroom_id, item_id, expiry),
        ).fetchone()
    if row:
        conn.execute(
            "UPDATE batch SET quantity = quantity + ?, updated_at = ? WHERE id = ?",
            (int(qty), _now(), row["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO batch (storeroom_id, item_id, expiry, quantity, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (storeroom_id, item_id, expiry, int(qty), _now()),
        )


def stock_in(storeroom_id, item_id, qty, expiry=None, user_id=None,
             kind=K_STOCK_IN, reason=None, counterpart=None):
    """Add stock. An expiry already on the shelf has its count increased."""
    qty = int(qty)
    if qty <= 0:
        return 0
    conn = _connect()
    try:
        with conn:
            _add_to_batch(conn, storeroom_id, item_id, qty, expiry)
            _log(conn, storeroom_id, item_id, qty, kind, expiry=expiry,
                 reason=reason, counterpart=counterpart, user_id=user_id)
        return qty
    finally:
        conn.close()


def _take_fefo(conn, storeroom_id, item_id, qty, kind, reason=None,
               counterpart=None, user_id=None):
    """Consume qty first-expiry-first-out. Returns (taken, shortfall, per_batch)."""
    want = int(qty)
    rows = conn.execute(
        """
        SELECT id, expiry, quantity FROM batch
        WHERE storeroom_id = ? AND item_id = ? AND quantity > 0
        ORDER BY (expiry IS NULL OR expiry = ''), expiry ASC, id ASC
        """,
        (storeroom_id, item_id),
    ).fetchall()

    remaining = want
    per_batch = []
    for r in rows:
        if remaining <= 0:
            break
        take = min(int(r["quantity"]), remaining)
        conn.execute(
            "UPDATE batch SET quantity = quantity - ?, updated_at = ? WHERE id = ?",
            (take, _now(), r["id"]),
        )
        # One movement row per batch, so the log names the exact expiry that left.
        _log(conn, storeroom_id, item_id, -take, kind, expiry=r["expiry"],
             reason=reason, counterpart=counterpart, user_id=user_id)
        per_batch.append({"expiry": r["expiry"], "qty": take})
        remaining -= take

    return want - remaining, remaining, per_batch


def withdraw(storeroom_id, item_id, qty, user_id=None):
    """Take stock out for use. Returns (taken, shortfall, per_batch)."""
    if int(qty) <= 0:
        return 0, 0, []
    conn = _connect()
    try:
        with conn:
            return _take_fefo(conn, storeroom_id, item_id, qty, K_WITHDRAW,
                              user_id=user_id)
    finally:
        conn.close()


def dispose(storeroom_id, item_id, qty, reason, user_id=None, expiry=None):
    """Throw stock away with a stated reason.

    When `expiry` is given, that specific batch is depleted (you dispose of the
    damaged or expired one you are holding, not whatever is oldest).
    """
    qty = int(qty)
    if qty <= 0:
        return 0, 0, []
    conn = _connect()
    try:
        with conn:
            if expiry is None:
                return _take_fefo(conn, storeroom_id, item_id, qty, K_DISPOSE,
                                  reason=reason, user_id=user_id)
            row = conn.execute(
                "SELECT id, quantity FROM batch WHERE storeroom_id = ? AND item_id = ? "
                "AND expiry IS ? AND quantity > 0",
                (storeroom_id, item_id, expiry),
            ).fetchone()
            if not row:
                return 0, qty, []
            take = min(int(row["quantity"]), qty)
            conn.execute(
                "UPDATE batch SET quantity = quantity - ?, updated_at = ? WHERE id = ?",
                (take, _now(), row["id"]),
            )
            _log(conn, storeroom_id, item_id, -take, K_DISPOSE, expiry=expiry,
                 reason=reason, user_id=user_id)
            return take, qty - take, [{"expiry": expiry, "qty": take}]
    finally:
        conn.close()


def transfer(src_storeroom_id, dst_storeroom_id, item_id, qty, user_id=None):
    """Move stock between storerooms, preserving each batch's expiry.

    The destination must already carry the item; transferring into a storeroom
    that was never assigned the item would create stock nobody is accountable
    for. Returns (moved, shortfall, per_batch, error).
    """
    qty = int(qty)
    if qty <= 0:
        return 0, 0, [], None
    if src_storeroom_id == dst_storeroom_id:
        return 0, qty, [], "Source and destination are the same storeroom."
    if not is_assigned(dst_storeroom_id, item_id):
        dst = get_storeroom(dst_storeroom_id)
        item = get_item(item_id)
        return 0, qty, [], (
            f"{dst['name'] if dst else 'Destination'} is not assigned "
            f"\"{item['name'] if item else 'this item'}\". An App Admin must assign it first."
        )

    conn = _connect()
    try:
        with conn:
            taken, short, per_batch = _take_fefo(
                conn, src_storeroom_id, item_id, qty, K_TRANSFER_OUT,
                counterpart=dst_storeroom_id, user_id=user_id,
            )
            for pb in per_batch:
                _add_to_batch(conn, dst_storeroom_id, item_id, pb["qty"], pb["expiry"])
                _log(conn, dst_storeroom_id, item_id, pb["qty"], K_TRANSFER_IN,
                     expiry=pb["expiry"], counterpart=src_storeroom_id, user_id=user_id)
        return taken, short, per_batch, None
    finally:
        conn.close()


def adjust_batch(storeroom_id, item_id, expiry, new_qty, user_id=None, reason=None):
    """Set a batch to an exact quantity (stock-take correction)."""
    new_qty = max(0, int(new_qty))
    conn = _connect()
    try:
        with conn:
            if expiry is None:
                row = conn.execute(
                    "SELECT id, quantity FROM batch WHERE storeroom_id = ? AND item_id = ? "
                    "AND expiry IS NULL", (storeroom_id, item_id)).fetchone()
            else:
                row = conn.execute(
                    "SELECT id, quantity FROM batch WHERE storeroom_id = ? AND item_id = ? "
                    "AND expiry = ?", (storeroom_id, item_id, expiry)).fetchone()
            old = int(row["quantity"]) if row else 0
            delta = new_qty - old
            if row:
                conn.execute(
                    "UPDATE batch SET quantity = ?, updated_at = ? WHERE id = ?",
                    (new_qty, _now(), row["id"]),
                )
            elif new_qty:
                _add_to_batch(conn, storeroom_id, item_id, new_qty, expiry)
            if delta:
                _log(conn, storeroom_id, item_id, delta, K_ADJUST, expiry=expiry,
                     reason=reason, user_id=user_id)
            return delta
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Activity
# --------------------------------------------------------------------------
def activity(storeroom_id, limit=300, kinds=None):
    sql = """
        SELECT m.*, i.name AS item_name, i.uom,
               u.name AS user_name,
               cs.name AS counterpart_name
        FROM movement m
        JOIN item i ON i.id = m.item_id
        LEFT JOIN app_user u ON u.id = m.user_id
        LEFT JOIN storeroom cs ON cs.id = m.counterpart_storeroom_id
        WHERE m.storeroom_id = ?
    """
    args = [storeroom_id]
    if kinds:
        sql += " AND m.kind IN (%s)" % ",".join("?" * len(kinds))
        args.extend(kinds)
    sql += " ORDER BY m.id DESC LIMIT ?"
    args.append(int(limit))
    conn = _connect()
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# Webhook payloads
# --------------------------------------------------------------------------
def webhook_payload(storeroom_id, expiry_horizon_days=31):
    """The body a scheduler would POST to the storeroom's webhook.

    Covers both triggers asked for: stock below minimum, and items expiring
    inside the horizon (a month by default).
    """
    room = get_storeroom(storeroom_id)
    low = low_stock(storeroom_id)
    soon = expiring(storeroom_id, within_days=expiry_horizon_days)
    return {
        "storeroom": room["name"] if room else None,
        "storeroom_id": storeroom_id,
        "generated_at": _now(),
        "expiry_horizon_days": expiry_horizon_days,
        "low_stock": [
            {"item": r["name"], "uom": r["uom"], "on_hand": r["on_hand"],
             "min_qty": r["min_qty"]}
            for r in low
        ],
        "expiring": [
            {"item": b["name"], "uom": b["uom"], "quantity": b["quantity"],
             "expiry": b["expiry"], "days_left": b["days_left"]}
            for b in soon
        ],
        "counts": {"low_stock": len(low), "expiring": len(soon)},
    }
