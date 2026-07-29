"""Day-to-day screens: add stock, withdraw, activity, transfer, dispose, views.

Add Stock and Withdraw commit as soon as the scanner settles — no confirm
button, by design. The scanner will not start a second batch until the frame
clears, which is what stops one basket being posted twice.

Dispose does confirm, deliberately: throwing stock away is the one action here
with no counterpart record to reconcile against, and it needs a stated reason.
"""

from datetime import date, datetime

import pandas as pd
import streamlit as st

import gs1
import store_db as db
from live_scanner import live_scanner, take_batch

EXPIRY_WARN_DAYS = 30


def _resolve(agency_id, raw_code):
    """Map a scanned payload to (item, expiry_from_code).

    A GS1 pharma code carries its GTIN plus expiry and lot, so scanning either
    the GS1 code or the plain GTIN lands on the same item.
    """
    info = gs1.extract(raw_code)
    code = info["gtin"] or raw_code
    item = db.item_by_barcode(agency_id, code)
    if item is None and code != raw_code:
        item = db.item_by_barcode(agency_id, raw_code)
    return item, info["expiry"]


def _fmt_expiry(v):
    return v or "—"


# ==========================================================================
# Add Stock
# ==========================================================================
def add_stock(agency_id, room, user):
    st.subheader("➕ Add Stock")
    st.caption(
        "Tip the items under the camera. Everything is counted at once — "
        "identical items are counted by how many of them the camera can see."
    )

    c1, c2 = st.columns([1, 1])
    with c1:
        has_exp = st.checkbox("These items have an expiry date", key="as_hasexp")
    with c2:
        exp = st.date_input("Expiry", value=date.today(), disabled=not has_exp,
                            key="as_exp")
    batch_expiry = exp.isoformat() if has_exp else None
    st.caption(
        "Set the expiry before scanning — it applies to everything in this batch. "
        "A GS1 pharma code that carries its own expiry overrides this per item."
    )

    value = live_scanner(key="scan_stock", mode="stock_in")
    batch = take_batch(value, "as_last_batch")

    if batch:
        added, unknown, unassigned = [], [], []
        for entry in batch["items"]:
            item, code_expiry = _resolve(agency_id, entry["code"])
            qty = int(entry["qty"])
            if item is None:
                unknown.append((entry["code"], qty))
                continue
            if not db.is_assigned(room["id"], item["id"]):
                unassigned.append((item, qty))
                continue
            expiry = (code_expiry.isoformat() if code_expiry else batch_expiry)
            db.stock_in(room["id"], item["id"], qty, expiry, user_id=user["id"])
            added.append({"Item": item["name"], "Qty": qty,
                          "UOM": item["uom"], "Expiry": _fmt_expiry(expiry)})
        st.session_state["as_result"] = {
            "added": added, "unknown": unknown,
            "unassigned": [(i["name"], q) for i, q in unassigned],
            "at": datetime.now().strftime("%H:%M:%S"),
        }

    res = st.session_state.get("as_result")
    if res:
        total = sum(r["Qty"] for r in res["added"])
        if total:
            st.success(f"### ✓ Added {total} item{'s' if total != 1 else ''} to {room['name']}")
            st.dataframe(pd.DataFrame(res["added"]), width="stretch", hide_index=True)
        if res["unknown"]:
            st.error(
                "**Not in the master inventory list** — an App Admin must add "
                "these before they can be stocked:\n\n"
                + "\n".join(f"- `{c}` ×{q}" for c, q in res["unknown"])
            )
        if res["unassigned"]:
            st.warning(
                f"**Not assigned to {room['name']}** — an App Admin must assign "
                "them to this storeroom:\n\n"
                + "\n".join(f"- {n} ×{q}" for n, q in res["unassigned"])
            )
        if st.button("Clear", key="as_clear"):
            st.session_state.pop("as_result", None)
            st.rerun()


# ==========================================================================
# Withdraw
# ==========================================================================
def withdraw(agency_id, room, user):
    st.subheader("➖ Withdraw")
    st.caption(
        "Put what you are taking under the camera. It is counted and deducted "
        "automatically — oldest expiry first."
    )

    value = live_scanner(key="scan_withdraw", mode="withdraw")
    batch = take_batch(value, "wd_last_batch")

    if batch:
        rows, short, unknown = [], [], []
        for entry in batch["items"]:
            item, _ = _resolve(agency_id, entry["code"])
            qty = int(entry["qty"])
            if item is None:
                unknown.append((entry["code"], qty))
                continue
            taken, missing, per_batch = db.withdraw(
                room["id"], item["id"], qty, user_id=user["id"]
            )
            for pb in per_batch:
                rows.append({"Item": item["name"], "Qty": pb["qty"],
                             "UOM": item["uom"], "Expiry taken": _fmt_expiry(pb["expiry"])})
            if missing:
                short.append((item["name"], missing))
        st.session_state["wd_result"] = {
            "rows": rows, "short": short, "unknown": unknown,
            "at": datetime.now().strftime("%H:%M:%S"),
        }

    res = st.session_state.get("wd_result")
    if res:
        total = sum(r["Qty"] for r in res["rows"])
        if total:
            st.success(f"## ✓ Withdrawn {total} item{'s' if total != 1 else ''}")
            st.dataframe(pd.DataFrame(res["rows"]), width="stretch", hide_index=True)
            st.caption(f"Recorded at {res['at']} — inventory is already updated.")
        if res["short"]:
            st.error(
                "**More was scanned than the records hold.** Taken down to zero "
                "rather than negative — check the shelf against the count:\n\n"
                + "\n".join(f"- {n}: short {q}" for n, q in res["short"])
            )
        if res["unknown"]:
            st.error(
                "**Unrecognised barcode** — not in the master inventory list:\n\n"
                + "\n".join(f"- `{c}` ×{q}" for c, q in res["unknown"])
            )
        if st.button("Clear", key="wd_clear"):
            st.session_state.pop("wd_result", None)
            st.rerun()


# ==========================================================================
# Activity
# ==========================================================================
def activity(agency_id, room, user):
    st.subheader("🧾 Activity")
    st.caption("Every movement in and out of this storeroom.")

    kinds = st.multiselect(
        "Filter", options=list(db.KIND_LABELS.keys()),
        format_func=lambda k: db.KIND_LABELS[k], default=[], key="ac_kinds",
    )
    moves = db.activity(room["id"], limit=400, kinds=kinds or None)
    if not moves:
        st.info("Nothing recorded yet.")
        return

    rows = []
    for m in moves:
        rows.append({
            "When": m["at"].replace("T", " "),
            "Flow": "IN" if m["delta"] > 0 else "OUT",
            "Type": db.KIND_LABELS.get(m["kind"], m["kind"]),
            "Item": m["item_name"],
            "Qty": abs(m["delta"]),
            "UOM": m["uom"],
            "Expiry": _fmt_expiry(m["expiry"]),
            "Reason": m["reason"] or "—",
            "Other store": m["counterpart_name"] or "—",
            "By": m["user_name"] or "—",
        })
    df = pd.DataFrame(rows)

    def _hl(r):
        colour = "background-color: #e7f6ec" if r["Flow"] == "IN" else "background-color: #fdecea"
        return [colour] * len(r)

    st.dataframe(df.style.apply(_hl, axis=1), width="stretch", hide_index=True)

    ins = sum(r["Qty"] for r in rows if r["Flow"] == "IN")
    outs = sum(r["Qty"] for r in rows if r["Flow"] == "OUT")
    a, b, c = st.columns(3)
    a.metric("Inflow", ins)
    b.metric("Outflow", outs)
    c.metric("Movements", len(rows))


# ==========================================================================
# Transfer
# ==========================================================================
def transfer(agency_id, room, user):
    st.subheader("🔁 Transfer Stock")
    st.caption(f"Move stock from **{room['name']}** to another storeroom in your agency.")

    term = st.text_input("Search for the destination storeroom", key="tr_term",
                         placeholder="start typing a storeroom name…")
    matches = db.search_storerooms(agency_id, term, exclude_id=room["id"])
    if not matches:
        st.info("No storeroom matches that." if term else "Type to search.")
        return
    labels = [m["name"] for m in matches]
    pick = st.selectbox(f"Destination ({len(matches)} match"
                        f"{'es' if len(matches) != 1 else ''})", labels, key="tr_dest")
    dest = matches[labels.index(pick)]

    stock = [r for r in db.storeroom_items(room["id"]) if r["on_hand"] > 0]
    if not stock:
        st.info("Nothing on hand to transfer.")
        return
    ilabels = [f"{r['name']} — {r['on_hand']} {r['uom']}" for r in stock]
    ipick = st.selectbox("Item", ilabels, key="tr_item")
    row = stock[ilabels.index(ipick)]

    qty = st.number_input("Quantity", min_value=1, max_value=int(row["on_hand"]),
                          value=1, step=1, key="tr_qty")

    if st.button(f"Transfer to {dest['name']}", type="primary", key="tr_go"):
        moved, short, per_batch, err = db.transfer(
            room["id"], dest["id"], row["item_id"], int(qty), user_id=user["id"]
        )
        if err:
            st.error(err)
        else:
            st.success(f"### ✓ Sent {moved} × {row['name']} to {dest['name']}")
            st.dataframe(
                pd.DataFrame([{"Qty": p["qty"], "Expiry": _fmt_expiry(p["expiry"])}
                              for p in per_batch]),
                width="stretch", hide_index=True,
            )
            st.caption("Expiry dates travel with the stock.")
            if short:
                st.warning(f"{short} could not be sent — not enough on hand.")


# ==========================================================================
# Dispose
# ==========================================================================
DISPOSAL_REASONS = [
    "Expired",
    "Packaging integrity damaged",
    "Contaminated",
    "Dropped / physically damaged",
    "Recalled by supplier",
    "Cold-chain breach",
    "Other (describe below)",
]


def dispose(agency_id, room, user):
    st.subheader("🗑 Dispose of Stock")
    st.caption("Take stock off the shelf permanently. A reason is required.")

    mode = st.radio("Find the item by", ["Select from list", "Scan it"],
                    horizontal=True, key="dp_mode")

    item_row = None
    stock = [r for r in db.storeroom_items(room["id"]) if r["on_hand"] > 0]
    if not stock:
        st.info("Nothing on hand to dispose of.")
        return

    if mode == "Scan it":
        value = live_scanner(key="scan_dispose", mode="dispose")
        batch = take_batch(value, "dp_last_batch")
        if batch:
            first = batch["items"][0]
            item, _ = _resolve(agency_id, first["code"])
            if item is None:
                st.error(f"`{first['code']}` is not in the master inventory list.")
            else:
                st.session_state["dp_scanned_item"] = item["id"]
        sid = st.session_state.get("dp_scanned_item")
        if sid:
            hits = [r for r in stock if r["item_id"] == sid]
            if hits:
                item_row = hits[0]
                st.info(f"Scanned: **{item_row['name']}** — {item_row['on_hand']} on hand")
            else:
                st.warning("That item has nothing on hand in this storeroom.")
    else:
        labels = [f"{r['name']} — {r['on_hand']} {r['uom']}" for r in stock]
        pick = st.selectbox("Item", labels, key="dp_item")
        item_row = stock[labels.index(pick)]

    if item_row is None:
        return

    lots = db.batches(room["id"], item_row["item_id"])
    lot_labels = ["Oldest expiry first (recommended)"] + [
        f"{_fmt_expiry(b['expiry'])} — {b['quantity']} on hand" for b in lots
    ]
    lot_pick = st.selectbox("Which batch?", lot_labels, key="dp_lot")
    chosen = None if lot_pick.startswith("Oldest") else lots[lot_labels.index(lot_pick) - 1]
    max_qty = int(chosen["quantity"]) if chosen else int(item_row["on_hand"])

    qty = st.number_input("Quantity to dispose", min_value=1, max_value=max_qty,
                          value=1, step=1, key="dp_qty")
    reason = st.selectbox("Reason", DISPOSAL_REASONS, key="dp_reason")
    note = st.text_input("Details", key="dp_note",
                         placeholder="e.g. plastic thorn on the outer wrap")

    full_reason = reason if not note.strip() else f"{reason}: {note.strip()}"
    needs_note = reason.startswith("Other") and not note.strip()

    st.warning(
        f"About to dispose of **{qty} × {item_row['name']}**"
        + (f" from the {_fmt_expiry(chosen['expiry'])} batch" if chosen else "")
        + ". This cannot be undone."
    )
    if st.button("Confirm disposal", type="primary", key="dp_go", disabled=needs_note):
        if needs_note:
            st.error("Describe the reason.")
        else:
            taken, short, per_batch = db.dispose(
                room["id"], item_row["item_id"], int(qty), full_reason,
                user_id=user["id"], expiry=(chosen["expiry"] if chosen else None),
            )
            if taken:
                st.success(f"### ✓ Disposed {taken} × {item_row['name']}")
                st.dataframe(
                    pd.DataFrame([{"Qty": p["qty"], "Expiry": _fmt_expiry(p["expiry"]),
                                   "Reason": full_reason} for p in per_batch]),
                    width="stretch", hide_index=True,
                )
                st.session_state.pop("dp_scanned_item", None)
            if short:
                st.warning(f"{short} could not be disposed of — not enough on hand.")
    if needs_note:
        st.caption("Describe the reason to enable the button.")


# ==========================================================================
# Inventory / Low stock / Expiry
# ==========================================================================
def inventory(agency_id, room, user):
    st.subheader("📦 Inventory")
    rows = db.storeroom_items(room["id"])
    if not rows:
        st.info("No items assigned to this storeroom yet. An App Admin assigns them.")
        return

    exp_by_item = {}
    for b in db.batches(room["id"]):
        exp_by_item.setdefault(b["item_id"], []).append(b)

    table = []
    for r in rows:
        lots = exp_by_item.get(r["item_id"], [])
        soonest = next((b["expiry"] for b in lots if b["expiry"]), None)
        table.append({
            "Item": r["name"],
            "On hand": r["on_hand"],
            "UOM": r["uom"],
            "Min": r["min_qty"] if r["min_qty"] is not None else "—",
            "Below min": r["below_min"],
            "Next expiry": _fmt_expiry(soonest),
            "Batches": len(lots),
        })
    df = pd.DataFrame(table)

    def _hl(r):
        return ["background-color: #fdecea" if r["Below min"] else ""] * len(r)

    st.dataframe(df.style.apply(_hl, axis=1), width="stretch", hide_index=True)

    a, b = st.columns(2)
    a.metric("Distinct items", len(rows))
    b.metric("Total units", sum(r["on_hand"] for r in rows))


def low_stock(agency_id, room, user):
    st.subheader("⚠️ Low Stock")
    rows = db.low_stock(room["id"])
    if not rows:
        st.success("Nothing below its minimum.")
        return
    st.error(f"{len(rows)} item{'s' if len(rows) != 1 else ''} below minimum.")
    st.dataframe(
        pd.DataFrame([{
            "Item": r["name"], "On hand": r["on_hand"], "UOM": r["uom"],
            "Min": r["min_qty"], "Short by": r["min_qty"] - r["on_hand"],
        } for r in rows]),
        width="stretch", hide_index=True,
    )


def expiry(agency_id, room, user):
    st.subheader("⏰ Expiry")
    rows = db.expiring(room["id"])
    if not rows:
        st.info("No dated stock on hand.")
        return

    table = []
    for b in rows:
        state = ("Expired" if b["days_left"] < 0
                 else "Expiring soon" if b["days_left"] <= EXPIRY_WARN_DAYS else "OK")
        table.append({
            "Item": b["name"], "Qty": b["quantity"], "UOM": b["uom"],
            "Expiry": b["expiry"], "Days left": b["days_left"], "Status": state,
        })
    df = pd.DataFrame(table)

    def _hl(r):
        if r["Status"] == "Expired":
            return ["background-color: #f5c6cb"] * len(r)
        if r["Status"] == "Expiring soon":
            return ["background-color: #fde9c8"] * len(r)
        return [""] * len(r)

    st.dataframe(df.style.apply(_hl, axis=1), width="stretch", hide_index=True)
    expired = [t for t in table if t["Status"] == "Expired"]
    soon = [t for t in table if t["Status"] == "Expiring soon"]
    if expired:
        st.error(f"{len(expired)} batch(es) already expired — dispose of them.")
    if soon:
        st.warning(f"{len(soon)} batch(es) expiring within {EXPIRY_WARN_DAYS} days.")
