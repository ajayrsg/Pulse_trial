"""Demo data.

Kept importable rather than buried in the entry script because the hosted
database is ephemeral — it resets whenever the app sleeps or redeploys — so
setting a storeroom up by hand is something you would otherwise redo often.
Reachable from the first-run screen and from Admin when no storerooms exist.
"""

from datetime import date, timedelta

import store_db as db

ITEMS = [
    # name, barcode, uom, min_qty, [(qty, expiry_offset_days or None)]
    ("Syringe 5ml", "5012345678900", "EA", 20, [(30, 20), (25, 400)]),
    ("Nitrile glove M", "5012345678901", "BX", 5, [(2, None)]),
    ("Gauze pad", "5012345678902", "PK", 10, [(12, 400), (4, -7)]),
    ("Saline 0.9% 500ml", "5012345678903", "BO", 8, [(9, 20)]),
    ("Micropore tape", "5012345678904", "RL", 4, [(1, None)]),
    ("Cannula 20G", "5012345678905", "EA", 6, []),          # assigned, no stock
]


def seed(agency_id, keep_existing_users=False):
    """Create two storerooms, users covering all three roles, and stock.

    Deliberately includes an out-of-stock item, an expired batch and two items
    under their minimum, so every screen has something to show.
    """
    a = agency_id
    r1 = db.create_storeroom(a, "Ward 5A Store")
    r2 = db.create_storeroom(a, "Ward 7B Store")

    existing = {u["role"] for u in db.list_users(a)} if keep_existing_users else set()

    admin = (None if db.ROLE_APP_ADMIN in existing
             else db.create_user(a, "Admin (demo)", db.ROLE_APP_ADMIN))
    lead = db.create_user(a, "Team Admin (demo)", db.ROLE_TEAM_ADMIN)
    nurse = db.create_user(a, "Nurse (demo)", db.ROLE_USER)

    # Everyone already in the agency gets both rooms, so an admin who just
    # created themselves is not left staring at an empty storeroom list.
    for u in db.list_users(a):
        db.assign_user(u["id"], r1)
        db.assign_user(u["id"], r2)

    def when(offset):
        return None if offset is None else (date.today() + timedelta(days=offset)).isoformat()

    for name, bc, u, minq, lots in ITEMS:
        iid = db.create_item(a, name, bc, u)
        db.assign_item(r1, iid, min_qty=minq)
        db.assign_item(r2, iid, min_qty=max(1, minq // 2))
        for qty, off in lots:
            db.stock_in(r1, iid, qty, when(off), user_id=nurse)

    syr = db.item_by_barcode(a, "5012345678900")["id"]
    db.stock_in(r2, syr, 5, when(20), user_id=nurse)
    db.withdraw(r1, syr, 6, user_id=nurse)
    return r1, r2
