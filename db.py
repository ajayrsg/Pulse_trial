"""SQLite storage: snapshots, per-item counts, and the transaction log.

- snapshots:   one row per capture+analysis attempt (including failed ones,
               so the audit trail is complete).
- item_counts: per detected item, for snapshots that parsed successfully.
- transactions: per-item count changes vs the previous parsed snapshot.
"""

import json
import os
import sqlite3

from poc_config import match_to_catalog

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inventory.db")


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _connect()
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                image_path TEXT,
                audit_path TEXT,
                parsed_ok INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                notes TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS item_counts (
                snapshot_id INTEGER NOT NULL,
                detected_name TEXT,
                matched_item TEXT,
                count INTEGER NOT NULL DEFAULT 0,
                confidence TEXT,
                expiry_dates_json TEXT,
                FOREIGN KEY (snapshot_id) REFERENCES snapshots(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                snapshot_id INTEGER NOT NULL,
                item_name TEXT NOT NULL,
                count_before INTEGER NOT NULL,
                count_after INTEGER NOT NULL,
                delta INTEGER NOT NULL,
                FOREIGN KEY (snapshot_id) REFERENCES snapshots(id)
            )
            """
        )
    conn.close()


def _coerce_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _catalog_counts_for_snapshot(conn, snapshot_id):
    """Return {catalog_item: total_count} for one snapshot (matched items only)."""
    rows = conn.execute(
        "SELECT matched_item, count FROM item_counts WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchall()
    counts = {}
    for r in rows:
        if r["matched_item"]:
            counts[r["matched_item"]] = counts.get(r["matched_item"], 0) + r["count"]
    return counts


def _previous_parsed_snapshot_id(conn, before_id):
    row = conn.execute(
        "SELECT id FROM snapshots WHERE parsed_ok = 1 AND id < ? ORDER BY id DESC LIMIT 1",
        (before_id,),
    ).fetchone()
    return row["id"] if row else None


def record_snapshot(ts_iso, image_path, audit_path, parsed, error):
    """Insert a snapshot; if parsed, store item counts and compute transactions.

    Returns the new snapshot id.
    """
    init_db()
    conn = _connect()
    parsed_ok = 1 if parsed is not None else 0
    notes = parsed.get("notes") if isinstance(parsed, dict) else None

    with conn:
        cur = conn.execute(
            "INSERT INTO snapshots (ts, image_path, audit_path, parsed_ok, error, notes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ts_iso, image_path, audit_path, parsed_ok, error, notes),
        )
        snapshot_id = cur.lastrowid

        if parsed_ok:
            items = parsed.get("items", []) if isinstance(parsed, dict) else []
            for item in items:
                if not isinstance(item, dict):
                    continue
                detected = item.get("name")
                matched = match_to_catalog(detected)
                count = _coerce_int(item.get("count"))
                confidence = item.get("confidence")
                expiry = item.get("expiry_dates_found") or []
                conn.execute(
                    "INSERT INTO item_counts "
                    "(snapshot_id, detected_name, matched_item, count, confidence, expiry_dates_json) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (snapshot_id, detected, matched, count, confidence, json.dumps(expiry)),
                )

            # Transaction diff vs the previous parsed snapshot.
            prev_id = _previous_parsed_snapshot_id(conn, snapshot_id)
            new_counts = _catalog_counts_for_snapshot(conn, snapshot_id)
            prev_counts = (
                _catalog_counts_for_snapshot(conn, prev_id) if prev_id else {}
            )
            all_items = set(new_counts) | set(prev_counts)
            for item_name in sorted(all_items):
                before = prev_counts.get(item_name, 0)
                after = new_counts.get(item_name, 0)
                delta = after - before
                if delta != 0:
                    conn.execute(
                        "INSERT INTO transactions "
                        "(ts, snapshot_id, item_name, count_before, count_after, delta) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (ts_iso, snapshot_id, item_name, before, after, delta),
                    )

    conn.close()
    return snapshot_id


def latest_parsed_snapshot():
    conn = _connect()
    row = conn.execute(
        "SELECT * FROM snapshots WHERE parsed_ok = 1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def item_counts_for_snapshot(snapshot_id):
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM item_counts WHERE snapshot_id = ?", (snapshot_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def all_snapshots():
    conn = _connect()
    rows = conn.execute("SELECT * FROM snapshots ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def all_transactions():
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM transactions ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
