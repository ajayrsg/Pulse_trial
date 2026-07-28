"""Ward store inventory web-app (phone/tablet friendly, no login).

Run:  streamlit run inventory_app.py

Two audiences, deliberately separated:

  ADMIN  — "Inventory" and "Expiry" tabs. Register items, set minimum stock,
           receive stock (with expiry/lot), print a barcode label for items
           that don't carry one, and correct mistakes.

  USER   — "Consume" and "Add Back" tabs. Point the camera at a basket; codes
           accumulate live. Nothing is committed until the quantities are
           confirmed, because a camera cannot distinguish "the same item still
           in frame" from "a second identical item".

Consumption depletes stock first-expiry-first-out. Returns rejoin the
soonest-expiring batch so they keep their original expiry.
"""

from datetime import date, datetime

import pandas as pd
import streamlit as st

import barcode_gen
import gs1
import store_db
from live_scanner import live_scanner

st.set_page_config(page_title="Ward Store", layout="centered")
store_db.init_db()

EXPIRY_WARN_DAYS = 30


def _parse_iso(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _normalize_scan(raw):
    """Map a decoded payload to (barcode, expiry, lot).

    A GS1 code (pharma DataMatrix/QR) carries its GTIN plus expiry and lot, so
    scanning either the GS1 code or the plain GTIN resolves to the same product.
    """
    info = gs1.extract(raw)
    return (info["gtin"] or raw), info["expiry"], info["lot"]


st.title("🏥 Ward Store")

tab_inv, tab_exp, tab_use, tab_back, tab_log = st.tabs(
    ["📦 Inventory", "⏰ Expiry", "➖ Consume", "➕ Add Back", "🧾 Activity"]
)


# ==========================================================================
# Inventory — admin: register, manage, receive stock, print labels
# ==========================================================================
with tab_inv:
    summary = store_db.inventory_summary()

    st.subheader("Current inventory")
    if not summary:
        st.info("No items yet. Register your first item under **Add an item** below.")
    else:
        df = pd.DataFrame(
            [
                {
                    "Item": r["name"],
                    "On hand": r["on_hand"],
                    "Min": r["min_stock"],
                    "Below min": r["below_min"],
                    "Barcode": r["barcode"],
                }
                for r in summary
            ]
        )

        def _hl(row):
            c = "background-color: #f8d7da" if row["Below min"] else ""
            return [c] * len(row)

        st.dataframe(df.style.apply(_hl, axis=1), width="stretch", hide_index=True)
        low = [r["name"] for r in summary if r["below_min"]]
        if low:
            st.warning("Below minimum: " + ", ".join(low))

    st.divider()

    # ---------------------------------------------------------------- add item
    st.subheader("Add an item")

    source = st.radio(
        "Does the item already have a barcode on it?",
        ["🏷️ No — generate one to print and stick on", "📇 Yes — type or paste it"],
        key="add_source",
    )
    generate = source.startswith("🏷️")

    with st.form("add_item"):
        name = st.text_input("Item name")
        min_stock = st.number_input("Minimum stock (warn below this)", min_value=0, value=2, step=1)

        typed_code = ""
        if not generate:
            typed_code = st.text_input("Barcode on the item")
        else:
            st.caption(
                f"A unique `{store_db.INTERNAL_PREFIX}-XXXXXX` code is minted on save, "
                "with a label to print."
            )

        st.markdown("**Opening stock** (optional — you can also receive stock later)")
        open_qty = st.number_input("Quantity", min_value=0, value=0, step=1)
        has_exp = st.checkbox("Has an expiry date")
        exp_val = st.date_input("Expiry", value=date.today(), disabled=not has_exp)
        lot = st.text_input("Lot (optional)")

        submitted = st.form_submit_button("Save item", type="primary")

    if submitted:
        if not name.strip():
            st.error("Give the item a name.")
        elif not generate and not typed_code.strip():
            st.error("Enter the item's barcode, or switch to generating one.")
        else:
            code = store_db.next_internal_barcode() if generate else typed_code.strip()
            existing = store_db.get_product(code)
            if existing and not generate:
                st.error(
                    f"Barcode `{code}` is already registered to **{existing['name']}**. "
                    "Edit that item below instead."
                )
            else:
                store_db.register_product(code, name.strip(), int(min_stock))
                if open_qty > 0:
                    store_db.add_batch(
                        code, int(open_qty),
                        exp_val.isoformat() if has_exp else None,
                        lot.strip() or None,
                    )
                    store_db.log_movement_standalone(code, int(open_qty), "receive", "opening stock")
                st.success(f"Registered **{name.strip()}** as `{code}`.")
                if generate:
                    st.session_state["label_for"] = code
                st.rerun()

    # ------------------------------------------------------------------ labels
    just_made = st.session_state.get("label_for")
    if just_made:
        prod = store_db.get_product(just_made)
        if prod:
            st.success(
                f"Print a label for **{prod['name']}** — `{just_made}`. "
                "It's under **Barcode label** below, already open."
            )
        else:
            st.session_state.pop("label_for", None)

    st.divider()

    # ------------------------------------------------------------ manage items
    st.subheader("Manage items")
    products = store_db.list_products()
    if not products:
        st.caption("Nothing to manage yet.")
    else:
        labels = [f"{p['name']}  ({p['barcode']})" for p in products]
        default_idx = 0
        if just_made:
            for i, p in enumerate(products):
                if p["barcode"] == just_made:
                    default_idx = i
                    break
        pick = st.selectbox("Item", labels, index=default_idx)
        prod = products[labels.index(pick)]
        code = prod["barcode"]

        with st.expander("✏️ Edit name / minimum", expanded=False):
            with st.form(f"edit_{code}"):
                new_name = st.text_input("Name", value=prod["name"])
                new_min = st.number_input(
                    "Minimum stock", min_value=0, value=int(prod["min_stock"]), step=1
                )
                if st.form_submit_button("Save changes"):
                    if not new_name.strip():
                        st.error("Name cannot be empty.")
                    else:
                        store_db.update_product(code, new_name.strip(), int(new_min))
                        st.success("Updated.")
                        st.rerun()

        with st.expander("📥 Receive stock", expanded=False):
            with st.form(f"recv_{code}"):
                st.write(f"Adding stock to **{prod['name']}**")
                rq = st.number_input("Quantity received", min_value=1, value=1, step=1)
                r_has_exp = st.checkbox("Has an expiry date", key=f"rexp_{code}")
                r_exp = st.date_input("Expiry", value=date.today(), disabled=not r_has_exp)
                r_lot = st.text_input("Lot (optional)")
                if st.form_submit_button("Receive", type="primary"):
                    store_db.add_batch(
                        code, int(rq),
                        r_exp.isoformat() if r_has_exp else None,
                        r_lot.strip() or None,
                    )
                    store_db.log_movement_standalone(code, int(rq), "receive", None)
                    st.success(f"Received {rq} × {prod['name']}.")
                    st.rerun()

        with st.expander("🏷️ Barcode label", expanded=bool(just_made)):
            if not barcode_gen.any_available():
                st.info(
                    "Label generation needs `python-barcode` (Code128) or `qrcode` (QR). "
                    "Neither is installed — everything else works."
                )
            else:
                opts = []
                if barcode_gen.code128_available():
                    opts.append("Code128 (standard barcode)")
                if barcode_gen.qr_available():
                    opts.append("QR (better on small or curved items)")
                choice = st.radio("Symbology", opts, key=f"sym_{code}")
                sym = "qr" if choice.startswith("QR") else "code128"
                try:
                    png = barcode_gen.make_label(code, prod["name"], sym)
                    st.image(png, caption=f"{prod['name']} — {code}", width=340)
                    st.download_button(
                        "⬇️ Download label (PNG)",
                        data=png,
                        file_name=f"label_{code}.png",
                        mime="image/png",
                    )
                    st.caption("Print at actual size, then stick it on the item or its shelf bin.")
                except Exception as e:
                    st.error(f"Could not generate the label: {e}")

        with st.expander("🗑 Delete item", expanded=False):
            st.warning(
                f"Deletes **{prod['name']}** and all of its stock records. Cannot be undone."
            )
            confirm = st.text_input(
                "Type the item name to confirm", key=f"del_{code}", placeholder=prod["name"]
            )
            if st.button("Delete permanently", key=f"delbtn_{code}"):
                if confirm.strip() == prod["name"]:
                    store_db.delete_product(code)
                    st.session_state.pop("label_for", None)
                    st.success("Deleted.")
                    st.rerun()
                else:
                    st.error("Name doesn't match — nothing was deleted.")

        st.divider()
        st.markdown("**Batches for this item** (correct mistakes here)")
        batches = store_db.product_batches(code)
        if not batches:
            st.caption("No stock recorded.")
        else:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "id": b["id"], "Qty": b["quantity"],
                            "Expiry": b["expiry"] or "—", "Lot": b["lot"] or "—",
                            "Added": b["added_at"],
                        }
                        for b in batches
                    ]
                ),
                width="stretch", hide_index=True,
            )
            c1, c2, c3 = st.columns([1, 1, 1])
            with c1:
                bid = st.selectbox("Batch id", [b["id"] for b in batches], key=f"bid_{code}")
            with c2:
                nq = st.number_input("Set qty to", min_value=0, value=0, step=1, key=f"nq_{code}")
            with c3:
                st.write("")
                st.write("")
                if st.button("Update", key=f"up_{code}"):
                    store_db.set_batch_quantity(bid, int(nq))
                    st.rerun()
            if st.button("🗑 Delete this batch", key=f"db_{code}"):
                store_db.delete_batch(bid)
                st.rerun()


# ==========================================================================
# Expiry
# ==========================================================================
with tab_exp:
    st.subheader("Expiry — soonest first")
    batches = store_db.batches_with_expiry()
    if not batches:
        st.info("No expiry dates recorded yet.")
    else:
        today = date.today()
        rows = []
        for b in batches:
            d = _parse_iso(b["expiry"])
            if d is None:
                continue
            days = (d - today).days
            flag = "expired" if days < 0 else ("soon" if days <= EXPIRY_WARN_DAYS else "ok")
            rows.append(
                {
                    "Item": b["name"], "Qty": b["quantity"], "Expiry": b["expiry"],
                    "Days left": days, "Lot": b["lot"] or "—", "_flag": flag,
                }
            )
        if rows:
            df = pd.DataFrame(rows)

            def _hl(row):
                c = "background-color: #f8d7da" if row["_flag"] in ("expired", "soon") else ""
                return [c] * len(row)

            st.dataframe(
                df.style.apply(_hl, axis=1),
                width="stretch", hide_index=True,
                column_config={"_flag": None},
            )
            st.caption(f"Red = expired or within {EXPIRY_WARN_DAYS} days.")


# ==========================================================================
# Shared scan-cart machinery for Consume / Add Back
# ==========================================================================
def _cart_keys(mode):
    return f"{mode}_cart", f"{mode}_seen"


def _ingest_scans(mode, scanned):
    """Fold newly-seen codes into the cart at qty 1.

    Tracks which codes have already been folded in, so a line the user removes
    stays removed instead of reappearing on the next rerun (the component keeps
    reporting its full accumulated list).
    """
    cart_key, seen_key = _cart_keys(mode)
    cart = st.session_state.setdefault(cart_key, {})
    seen = st.session_state.setdefault(seen_key, set())

    fresh = 0
    for item in scanned:
        raw = item["code"]
        if raw in seen:
            continue
        seen.add(raw)
        code, expiry, lot = _normalize_scan(raw)
        entry = cart.setdefault(
            code, {"qty": 0, "expiry": expiry, "lot": lot, "raw": raw}
        )
        entry["qty"] += 1
        if expiry and not entry.get("expiry"):
            entry["expiry"] = expiry
        if lot and not entry.get("lot"):
            entry["lot"] = lot
        fresh += 1
    return fresh


def _reset_cart(mode):
    cart_key, seen_key = _cart_keys(mode)
    st.session_state[cart_key] = {}
    st.session_state[seen_key] = set()


def _scan_flow(mode, verb, help_text):
    """Render a scan → review → confirm flow. mode is 'consume' or 'addback'."""
    cart_key, _ = _cart_keys(mode)

    st.caption(help_text)

    scanned = live_scanner(key=f"scanner_{mode}")
    _ingest_scans(mode, scanned or [])

    # Fallback path. The camera can be unavailable for mundane reasons — denied
    # permission, no HTTPS, a locked-down network blocking the scanner library.
    # The printed label carries the code in text for exactly this case, so there
    # is always a way to work without a camera.
    with st.expander("⌨️ No camera? Enter a code by hand"):
        with st.form(f"manual_{mode}", clear_on_submit=True):
            m_code = st.text_input("Code printed on the label or barcode")
            m_qty = st.number_input("Quantity", min_value=1, value=1, step=1)
            if st.form_submit_button("Add to list"):
                typed = m_code.strip()
                if not typed:
                    st.error("Enter a code.")
                else:
                    norm, expiry, lot = _normalize_scan(typed)
                    cart = st.session_state.setdefault(cart_key, {})
                    entry = cart.setdefault(
                        norm, {"qty": 0, "expiry": expiry, "lot": lot, "raw": typed}
                    )
                    entry["qty"] += int(m_qty)
                    st.rerun()

    cart = st.session_state.get(cart_key, {})
    if not cart:
        st.info(
            "Press **Start scanning** and point the camera at your items — "
            "or add a code by hand above."
        )
        return

    st.divider()
    st.subheader(f"Review before {verb.lower()}")
    st.caption(
        "Each distinct barcode is listed once. If you have several identical items, "
        "set the quantity here — the camera can't count duplicates for you."
    )

    known, unknown = [], []
    for code, entry in cart.items():
        prod = store_db.get_product(code)
        (known if prod else unknown).append((code, entry, prod))

    edited = None
    if known:
        rows = []
        for code, entry, prod in known:
            rows.append(
                {
                    "Item": prod["name"],
                    "Qty": int(entry["qty"]),
                    "On hand": store_db.on_hand(code),
                    "Barcode": code,
                    "Remove": False,
                }
            )
        edited = st.data_editor(
            pd.DataFrame(rows),
            width="stretch",
            hide_index=True,
            num_rows="fixed",
            disabled=["Item", "On hand", "Barcode"],
            column_config={
                "Qty": st.column_config.NumberColumn(min_value=0, step=1),
                "Remove": st.column_config.CheckboxColumn(help="Exclude this line"),
            },
            key=f"editor_{mode}",
        )

    if unknown:
        st.warning(
            f"{len(unknown)} scanned code(s) aren't registered yet, so they can't be "
            f"{verb.lower()}d. Register them on the **Inventory** tab first."
        )
        for code, entry, _ in unknown:
            st.code(code, language=None)

    c1, c2 = st.columns([2, 1])
    with c1:
        go = st.button(
            f"✅ Confirm {verb.lower()}", type="primary",
            disabled=edited is None, key=f"go_{mode}",
        )
    with c2:
        if st.button("Clear list", key=f"clear_{mode}"):
            _reset_cart(mode)
            st.rerun()

    if go and edited is not None:
        results, shortfalls = [], []
        for _, row in edited.iterrows():
            if row["Remove"]:
                continue
            qty = int(row["Qty"])
            if qty <= 0:
                continue
            code = row["Barcode"]
            if mode == "consume":
                done, short = store_db.consume(code, qty)
                if done:
                    results.append(f"{done} × {row['Item']}")
                if short:
                    shortfalls.append(f"{row['Item']} (short {short})")
            else:
                store_db.add_back(code, qty)
                results.append(f"{qty} × {row['Item']}")

        if results:
            st.success(f"{verb}: " + ", ".join(results))
        if shortfalls:
            st.error(
                "Not enough stock on record for: " + ", ".join(shortfalls)
                + ". Stock was taken down to zero rather than negative — "
                "check the shelf against the count."
            )
        if not results and not shortfalls:
            st.info("Nothing to apply — every line was removed or zero.")
        else:
            _reset_cart(mode)


# ==========================================================================
# Consume
# ==========================================================================
with tab_use:
    st.subheader("Consume stock")
    _scan_flow(
        "consume", "Consumed",
        "Taking items out of the store. Prop the tablet over the basket and scan "
        "everything, then confirm. Oldest expiry is used up first.",
    )


# ==========================================================================
# Add Back
# ==========================================================================
with tab_back:
    st.subheader("Add stock back")
    _scan_flow(
        "addback", "Added back",
        "Returning unused items to the store. Scan everything going back on the "
        "shelf, then confirm.",
    )


# ==========================================================================
# Activity
# ==========================================================================
with tab_log:
    st.subheader("Recent activity")
    moves = store_db.recent_movements(100)
    if not moves:
        st.info("No stock movements recorded yet.")
    else:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "When": m["at"].replace("T", " "),
                        "Item": m["name"],
                        "Change": f"+{m['delta']}" if m["delta"] > 0 else str(m["delta"]),
                        "Type": {"consume": "Consumed", "add_back": "Added back",
                                 "receive": "Received"}.get(m["kind"], m["kind"]),
                        "Note": m["note"] or "—",
                    }
                    for m in moves
                ]
            ),
            width="stretch", hide_index=True,
        )
