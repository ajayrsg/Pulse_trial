"""Store inventory web-app (phone-friendly, no login).

Run:  streamlit run inventory_app.py
On your phone: open the Network URL Streamlit prints, on the same Wi-Fi.

Flows:
  - Inventory : current on-hand per product vs. min stock (red if below).
  - Expiry    : batches sorted soonest-first (red if expired / within 30 days).
  - Count     : (a) manual entry, or (b) scan with the camera. A scan decodes
                the barcode, looks up (or registers) the product, adds to the
                count, and fills expiry automatically when the code is a GS1
                code that carries it.

Barcodes give accurate identity + count. Expiry only comes from the scan for
GS1 codes (mostly medical); for plain retail codes you type it in.
"""

from datetime import date, datetime

import pandas as pd
import streamlit as st

import barcode_decode
import gs1
import store_db

st.set_page_config(page_title="Ward Store", layout="centered")
store_db.init_db()

EXPIRY_WARN_DAYS = 30


def _parse_iso(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


st.title("🏥 Ward Store")

tab_inv, tab_exp, tab_count = st.tabs(["📦 Inventory", "⏰ Expiry", "➕ Count"])


# --------------------------------------------------------------------------
# Inventory
# --------------------------------------------------------------------------
with tab_inv:
    st.subheader("Current inventory")
    summary = store_db.inventory_summary()
    if not summary:
        st.info("No products yet. Go to **Count** to add stock (manually or by camera).")
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


# --------------------------------------------------------------------------
# Expiry
# --------------------------------------------------------------------------
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
                    "Item": b["name"],
                    "Qty": b["quantity"],
                    "Expiry": b["expiry"],
                    "Days left": days,
                    "Lot": b["lot"] or "—",
                    "_flag": flag,
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


# --------------------------------------------------------------------------
# Count
# --------------------------------------------------------------------------
def _add_form(barcode, prefill_name="", prefill_min=0, prefill_expiry=None,
              prefill_lot=None, known=False, key_suffix=""):
    """Render an add-to-inventory form. Returns True if something was added."""
    label = "Add to inventory" if known else "Register & add"
    with st.form(f"add_{barcode}_{key_suffix}"):
        st.write(f"**Barcode:** `{barcode}`")
        if known:
            name = prefill_name
            min_stock = prefill_min
            st.write(f"**Product:** {name}")
        else:
            st.info("New product — give it a name to register it.")
            name = st.text_input("Product name", value=prefill_name)
            min_stock = st.number_input("Min stock", min_value=0, value=int(prefill_min), step=1)

        qty = st.number_input("Quantity to add", min_value=1, value=1, step=1)

        has_exp = st.checkbox("Has expiry date", value=prefill_expiry is not None)
        exp_val = st.date_input(
            "Expiry", value=prefill_expiry or date.today(), disabled=not has_exp
        )
        lot = st.text_input("Lot (optional)", value=prefill_lot or "")

        submitted = st.form_submit_button(label, type="primary")

    if submitted:
        if not known and not name.strip():
            st.error("Please enter a product name to register this item.")
            return False
        if not store_db.get_product(barcode):
            store_db.register_product(barcode, name.strip(), min_stock)
        expiry_iso = exp_val.isoformat() if has_exp else None
        store_db.add_batch(barcode, qty, expiry_iso, lot.strip() or None)
        st.success(f"Added {qty} × {name or barcode}.")
        return True
    return False


with tab_count:
    st.subheader("Count inventory")
    method = st.radio("How do you want to count?", ["✍️ Manual", "📷 Scan with camera"])

    # ---------------- Manual ----------------
    if method == "✍️ Manual":
        products = store_db.list_products()
        choices = ["➕ New product…"] + [f"{p['name']}  ({p['barcode']})" for p in products]
        pick = st.selectbox("Product", choices)

        if pick == "➕ New product…":
            new_barcode = st.text_input("Barcode / code (type or leave blank for a manual id)")
            barcode = new_barcode.strip() or f"manual-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            _add_form(barcode, known=False, key_suffix="manual_new")
        else:
            idx = choices.index(pick) - 1
            p = products[idx]
            _add_form(
                p["barcode"], prefill_name=p["name"], prefill_min=p["min_stock"],
                known=True, key_suffix="manual_known",
            )

        st.divider()
        st.markdown("**Recorded batches** (adjust or remove)")
        batches = store_db.all_batches()
        if batches:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "id": b["id"], "Item": b["name"], "Qty": b["quantity"],
                            "Expiry": b["expiry"] or "—", "Lot": b["lot"] or "—",
                        }
                        for b in batches
                    ]
                ),
                width="stretch", hide_index=True,
            )
            ids = [b["id"] for b in batches]
            col1, col2, col3 = st.columns(3)
            with col1:
                edit_id = st.selectbox("Batch id", ids)
            with col2:
                new_qty = st.number_input("New qty", min_value=0, value=1, step=1)
            with col3:
                st.write("")
                st.write("")
                if st.button("Update qty"):
                    store_db.set_batch_quantity(edit_id, new_qty)
                    st.rerun()
            if st.button("🗑 Delete selected batch"):
                store_db.delete_batch(edit_id)
                st.rerun()
        else:
            st.caption("No batches yet.")

    # ---------------- Camera scan ----------------
    else:
        st.caption(
            "Scan one item at a time. The photo is decoded on the device; a GS1 "
            "medical code also fills in expiry + lot automatically."
        )
        if not barcode_decode.datamatrix_available():
            st.caption(
                "ℹ️ DataMatrix (2D pharma) decoding isn't installed, so those codes "
                "won't read yet — 1D barcodes and QR codes work. Everything else is unaffected."
            )
        photo = st.camera_input("Scan barcode", label_visibility="collapsed")
        if photo is not None:
            decoded = barcode_decode.decode_image_bytes(photo.getvalue())
            if not decoded:
                st.warning(
                    "No barcode found in that image. Fill the frame with the barcode, "
                    "hold steady, and ensure good lighting — then retake."
                )
            else:
                if len(decoded) > 1:
                    st.info(f"{len(decoded)} codes found — handling the first. Scan one at a time for best results.")
                first = decoded[0]
                st.write(f"**Decoded:** `{first['data']}`  ({first['symbology']})")
                info = gs1.extract(first["data"])
                barcode = info["gtin"] or first["data"]
                if info["expiry"] or info["lot"]:
                    st.success(
                        "GS1 code — read "
                        + (f"expiry {info['expiry']} " if info["expiry"] else "")
                        + (f"lot {info['lot']}" if info["lot"] else "")
                    )
                product = store_db.get_product(barcode)
                if product:
                    _add_form(
                        barcode, prefill_name=product["name"], prefill_min=product["min_stock"],
                        prefill_expiry=info["expiry"], prefill_lot=info["lot"],
                        known=True, key_suffix="scan_known",
                    )
                else:
                    _add_form(
                        barcode, prefill_expiry=info["expiry"], prefill_lot=info["lot"],
                        known=False, key_suffix="scan_new",
                    )
