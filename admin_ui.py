"""App Admin screens — intended for desktop: storerooms, master inventory, users.

Three responsibilities, matching the App Admin role: build the master inventory
list and tag it to storerooms, manage storerooms and their webhooks, and manage
people and what they may do.
"""

import io
import json
import urllib.error
import urllib.request

import pandas as pd
import streamlit as st

import store_db as db
import uom as uom_mod

WEBHOOK_TIMEOUT_S = 10


# ==========================================================================
# Storerooms
# ==========================================================================
def storerooms(agency_id):
    st.subheader("🏬 Storerooms")

    with st.expander("➕ New storeroom", expanded=False):
        with st.form("new_room"):
            name = st.text_input("Storeroom name")
            hook = st.text_input("Webhook URL (optional)",
                                 placeholder="https://…/plumber-hook")
            if st.form_submit_button("Create", type="primary"):
                if not name.strip():
                    st.error("Give the storeroom a name.")
                else:
                    try:
                        db.create_storeroom(agency_id, name, hook)
                        st.success(f"Created {name.strip()}.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Could not create it: {e}")

    rooms = db.list_storerooms(agency_id)
    if not rooms:
        st.info("No storerooms yet. Create one above.")
        return

    st.dataframe(
        pd.DataFrame([{
            "Storeroom": r["name"],
            "Items assigned": len(db.storeroom_items(r["id"])),
            "Users": len(db.users_in_storeroom(r["id"])),
            "Webhook": "yes" if r["webhook_url"] else "—",
        } for r in rooms]),
        width="stretch", hide_index=True,
    )

    labels = [r["name"] for r in rooms]
    pick = st.selectbox("Open storeroom", labels, key="ad_room")
    room = rooms[labels.index(pick)]

    t_users, t_inv, t_hook, t_danger = st.tabs(
        ["👥 Users Assigned", "📦 Inventory Assigned", "🔔 Webhook", "⚙️ Settings"]
    )

    # -------------------------------------------------------------- users tab
    with t_users:
        assigned = db.users_in_storeroom(room["id"])
        if assigned:
            st.dataframe(
                pd.DataFrame([{
                    "Name": u["name"], "Email": u["email"] or "—",
                    "Role": db.ROLES.get(u["role"], u["role"]),
                } for u in assigned]),
                width="stretch", hide_index=True,
            )
        else:
            st.info("Nobody is assigned to this storeroom.")

        everyone = db.list_users(agency_id)
        unassigned = [u for u in everyone if u["id"] not in {a["id"] for a in assigned}]
        c1, c2 = st.columns(2)
        with c1:
            if unassigned:
                ulabels = [f"{u['name']} ({db.ROLES.get(u['role'], u['role'])})"
                           for u in unassigned]
                up = st.selectbox("Assign someone", ulabels, key=f"as_u_{room['id']}")
                if st.button("Assign", key=f"as_ub_{room['id']}"):
                    db.assign_user(unassigned[ulabels.index(up)]["id"], room["id"])
                    st.rerun()
            else:
                st.caption("Everyone is already assigned here.")
        with c2:
            if assigned:
                rlabels = [u["name"] for u in assigned]
                rp = st.selectbox("Unassign someone", rlabels, key=f"un_u_{room['id']}")
                target = assigned[rlabels.index(rp)]
                if st.button("Unassign", key=f"un_ub_{room['id']}"):
                    db.unassign_user(target["id"], room["id"])
                    st.rerun()
                if st.button(f"🗑 Delete {target['name']} entirely",
                             key=f"del_u_{room['id']}"):
                    db.delete_user(target["id"])
                    st.success(f"Deleted {target['name']}.")
                    st.rerun()

    # ---------------------------------------------------------- inventory tab
    with t_inv:
        rows = db.storeroom_items(room["id"])
        if rows:
            lots = {}
            for b in db.batches(room["id"]):
                lots.setdefault(b["item_id"], []).append(b)
            st.dataframe(
                pd.DataFrame([{
                    "Item": r["name"], "On hand": r["on_hand"], "UOM": r["uom"],
                    "Min": r["min_qty"] if r["min_qty"] is not None else "—",
                    "Next expiry": next((b["expiry"] for b in lots.get(r["item_id"], [])
                                         if b["expiry"]), "—"),
                    "Below min": r["below_min"],
                } for r in rows]),
                width="stretch", hide_index=True,
            )
        else:
            st.info("No items assigned to this storeroom.")

        st.markdown("**Assign an item from the master list**")
        master = db.list_items(agency_id)
        already = {r["item_id"] for r in rows}
        available = [i for i in master if i["id"] not in already]
        if not master:
            st.caption("The master inventory list is empty — build it first.")
        elif not available:
            st.caption("Every master item is already assigned here.")
        else:
            with st.form(f"assign_item_{room['id']}"):
                ilabels = [f"{i['name']} ({i['uom']})" for i in available]
                ip = st.selectbox("Item", ilabels)
                c1, c2, c3 = st.columns(3)
                with c1:
                    minq = st.number_input("Min qty (optional)", min_value=0, value=0, step=1)
                with c2:
                    curq = st.number_input("Current qty", min_value=0, value=0, step=1)
                with c3:
                    has_e = st.checkbox("Has expiry")
                    exp = st.date_input("Expiry", disabled=not has_e)
                if st.form_submit_button("Assign to storeroom", type="primary"):
                    item = available[ilabels.index(ip)]
                    db.assign_item(
                        room["id"], item["id"],
                        min_qty=(minq if minq > 0 else None),
                        opening_qty=int(curq),
                        expiry=(exp.isoformat() if has_e else None),
                    )
                    st.success(f"Assigned {item['name']}.")
                    st.rerun()

        if rows:
            st.divider()
            st.markdown("**Change a minimum, or unassign**")
            rlabels = [f"{r['name']} — {r['on_hand']} on hand" for r in rows]
            rp = st.selectbox("Assigned item", rlabels, key=f"chg_{room['id']}")
            target = rows[rlabels.index(rp)]
            c1, c2 = st.columns(2)
            with c1:
                newmin = st.number_input(
                    "Min qty", min_value=0,
                    value=int(target["min_qty"] or 0), step=1, key=f"nm_{room['id']}",
                )
                if st.button("Save minimum", key=f"nmb_{room['id']}"):
                    db.set_min_qty(room["id"], target["item_id"],
                                   newmin if newmin > 0 else None)
                    st.rerun()
            with c2:
                st.write("")
                st.write("")
                if st.button("Unassign item", key=f"unb_{room['id']}"):
                    ok, msg = db.unassign_item(room["id"], target["item_id"])
                    (st.success if ok else st.error)(msg)
                    if ok:
                        st.rerun()
                st.caption("An item with stock on hand cannot be unassigned.")

            # Add Stock and Withdraw commit straight from the camera with no
            # confirm step, so there has to be somewhere to fix a count that
            # came out wrong. Corrections are logged as adjustments.
            st.markdown("**Correct a count (stock take)**")
            lots = db.batches(room["id"], target["item_id"], with_zero=True)
            if not lots:
                st.caption("No batches recorded for this item.")
            else:
                llabels = [f"{b['expiry'] or 'no expiry'} — {b['quantity']} on hand"
                           for b in lots]
                lp = st.selectbox("Batch", llabels, key=f"cb_{room['id']}")
                lot = lots[llabels.index(lp)]
                cc1, cc2 = st.columns([1, 2])
                with cc1:
                    actual = st.number_input(
                        "Counted on the shelf", min_value=0,
                        value=int(lot["quantity"]), step=1, key=f"cq_{room['id']}",
                    )
                with cc2:
                    why = st.text_input("Note", key=f"cw_{room['id']}",
                                        placeholder="e.g. scanner undercounted a basket")
                if st.button("Save correction", key=f"cbtn_{room['id']}"):
                    delta = db.adjust_batch(
                        room["id"], target["item_id"], lot["expiry"], int(actual),
                        reason=(why.strip() or "stock take"),
                    )
                    if delta:
                        st.success(f"Adjusted by {delta:+d}.")
                    else:
                        st.info("No change.")
                    st.rerun()

    # ------------------------------------------------------------ webhook tab
    with t_hook:
        st.caption(
            "A scheduler (Plumber, cron, GitHub Actions — anything that can POST) "
            "calls this URL. Two reports are meant to go out: items expiring in the "
            "next month, at the start of each month, and anything below its minimum."
        )
        url = st.text_input("Webhook URL", value=room["webhook_url"] or "",
                            key=f"hook_{room['id']}")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Save URL", key=f"hooksave_{room['id']}"):
                db.update_storeroom(room["id"], webhook_url=url)
                st.success("Saved.")
                st.rerun()
        with c2:
            horizon = st.number_input("Expiry horizon (days)", min_value=1,
                                      value=31, step=1, key=f"hz_{room['id']}")

        payload = db.webhook_payload(room["id"], expiry_horizon_days=int(horizon))
        st.markdown("**Payload that would be sent**")
        st.json(payload, expanded=False)

        if st.button("Send now (test)", key=f"hooksend_{room['id']}",
                     disabled=not (room["webhook_url"] or url).strip()):
            target = (url or room["webhook_url"]).strip()
            body = json.dumps(payload).encode()
            req = urllib.request.Request(
                target, data=body, headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=WEBHOOK_TIMEOUT_S) as r:
                    st.success(f"POSTed — {r.status} {r.reason}")
            except urllib.error.HTTPError as e:
                st.error(f"Endpoint returned {e.code}: {e.reason}")
            except Exception as e:
                st.error(f"Could not reach it: {e}")

        st.info(
            "**This app cannot schedule anything.** Streamlit only runs while a "
            "browser session is open, so the monthly and low-stock triggers must "
            "come from an external scheduler calling in. The payload above is what "
            "to expect."
        )

    # ------------------------------------------------------------- settings tab
    with t_danger:
        newname = st.text_input("Rename storeroom", value=room["name"],
                               key=f"rn_{room['id']}")
        if st.button("Rename", key=f"rnb_{room['id']}"):
            db.update_storeroom(room["id"], name=newname)
            st.rerun()
        st.divider()
        st.warning("Deleting a storeroom removes its stock records and assignments.")
        conf = st.text_input("Type the storeroom name to confirm",
                            key=f"dc_{room['id']}", placeholder=room["name"])
        if st.button("Delete storeroom", key=f"db_{room['id']}"):
            if conf.strip() == room["name"]:
                db.delete_storeroom(room["id"])
                st.success("Deleted.")
                st.rerun()
            else:
                st.error("Name doesn't match — nothing was deleted.")


# ==========================================================================
# Master inventory list
# ==========================================================================
CSV_TEMPLATE = "name,barcode,uom\nSyringe 5ml,5012345678900,EA\nGauze pad,5012345678901,PK\n"


def master_inventory(agency_id):
    st.subheader("📋 Master Inventory List")
    st.caption(
        "Every item the agency stocks. Items live here first, then get assigned "
        "to the storerooms that should carry them."
    )

    with st.expander("➕ Add an item", expanded=False):
        with st.form("new_item"):
            name = st.text_input("Item name")
            barcode = st.text_input("Barcode", placeholder="scan or type the code on the pack")
            u = st.selectbox("Unit of measure", uom_mod.codes(),
                             format_func=uom_mod.display,
                             index=uom_mod.codes().index(uom_mod.DEFAULT_UOM))
            if st.form_submit_button("Add to master list", type="primary"):
                if not name.strip():
                    st.error("Give the item a name.")
                elif not barcode.strip():
                    st.error("A barcode is required — it is how the scanner finds the item.")
                elif db.item_by_barcode(agency_id, barcode.strip()):
                    st.error(f"Barcode `{barcode.strip()}` is already in the list.")
                else:
                    db.create_item(agency_id, name, barcode, u)
                    st.success(f"Added {name.strip()}.")
                    st.rerun()

    with st.expander("⬆️ Upload an existing list (CSV)", expanded=False):
        st.caption("Columns: `name`, `barcode`, `uom`. Unknown UOM codes fall back to EA.")
        st.download_button("Download template", CSV_TEMPLATE, "items_template.csv", "text/csv")
        up = st.file_uploader("CSV file", type=["csv"], key="mi_csv")
        if up is not None:
            try:
                raw = pd.read_csv(io.BytesIO(up.getvalue()), dtype=str).fillna("")
            except Exception as e:
                st.error(f"Could not read that CSV: {e}")
                raw = None
            if raw is not None:
                cols = {c.lower().strip(): c for c in raw.columns}
                if "name" not in cols:
                    st.error("The CSV needs at least a `name` column.")
                else:
                    st.dataframe(raw.head(20), width="stretch", hide_index=True)
                    st.caption(f"{len(raw)} row(s) in the file — showing the first 20.")
                    if st.button("Import", type="primary", key="mi_import"):
                        added = skipped = failed = 0
                        problems = []
                        for _, r in raw.iterrows():
                            nm = str(r[cols["name"]]).strip()
                            bc = str(r[cols["barcode"]]).strip() if "barcode" in cols else ""
                            uu = uom_mod.normalize(str(r[cols["uom"]]) if "uom" in cols else "")
                            if not nm:
                                failed += 1
                                continue
                            if bc and db.item_by_barcode(agency_id, bc):
                                skipped += 1
                                continue
                            try:
                                db.create_item(agency_id, nm, bc or None, uu)
                                added += 1
                            except Exception as e:
                                failed += 1
                                if len(problems) < 5:
                                    problems.append(f"{nm}: {e}")
                        st.success(f"Imported {added}. Skipped {skipped} already-known "
                                   f"barcode(s). {failed} row(s) failed.")
                        for p in problems:
                            st.caption(f"• {p}")
                        st.rerun()

    items = db.list_items(agency_id)
    if not items:
        st.info("The master list is empty.")
        return

    rooms = db.list_storerooms(agency_id)
    room_names = {r["id"]: r["name"] for r in rooms}
    tagged = {}
    for r in rooms:
        for si in db.storeroom_items(r["id"]):
            tagged.setdefault(si["item_id"], []).append(room_names[r["id"]])

    st.dataframe(
        pd.DataFrame([{
            "Item": i["name"], "Barcode": i["barcode"] or "—", "UOM": i["uom"],
            "Storerooms": ", ".join(tagged.get(i["id"], [])) or "— not assigned —",
            "Total on hand": db.item_total_on_hand(i["id"]),
        } for i in items]),
        width="stretch", hide_index=True,
    )

    st.divider()
    st.markdown("**Edit an item**")
    labels = [f"{i['name']} ({i['barcode'] or 'no barcode'})" for i in items]
    pick = st.selectbox("Item", labels, key="mi_edit")
    item = items[labels.index(pick)]

    with st.form(f"edit_item_{item['id']}"):
        nm = st.text_input("Name", value=item["name"])
        bc = st.text_input("Barcode", value=item["barcode"] or "")
        codes = uom_mod.codes()
        uu = st.selectbox("Unit of measure", codes, format_func=uom_mod.display,
                          index=codes.index(uom_mod.normalize(item["uom"])))
        if st.form_submit_button("Save"):
            clash = db.item_by_barcode(agency_id, bc.strip()) if bc.strip() else None
            if clash and clash["id"] != item["id"]:
                st.error(f"Barcode `{bc.strip()}` belongs to {clash['name']}.")
            else:
                db.update_item(item["id"], nm, bc, uu)
                st.success("Saved.")
                st.rerun()

    total = db.item_total_on_hand(item["id"])
    if total > 0:
        st.caption(f"Cannot delete — {total} on hand across storerooms.")
    else:
        if st.button(f"🗑 Delete {item['name']}", key=f"mi_del_{item['id']}"):
            db.delete_item(item["id"])
            st.success("Deleted.")
            st.rerun()


# ==========================================================================
# Users
# ==========================================================================
ROLE_HELP = {
    db.ROLE_APP_ADMIN: "Manages inventory, storerooms and users.",
    db.ROLE_TEAM_ADMIN: "Everything a User can do, plus dispose and transfer.",
    db.ROLE_USER: "Add stock, withdraw, and view activity, inventory, low stock and expiry.",
}


def users(agency_id):
    st.subheader("👥 Users")

    with st.expander("➕ New user", expanded=False):
        with st.form("new_user"):
            name = st.text_input("Name")
            email = st.text_input("Email (optional)")
            role = st.selectbox("Role", list(db.ROLES.keys()),
                                format_func=lambda r: db.ROLES[r])
            st.caption(ROLE_HELP[role] if role in ROLE_HELP else "")
            rooms = db.list_storerooms(agency_id)
            rlabels = [r["name"] for r in rooms]
            chosen = st.multiselect("Assign to storerooms", rlabels)
            if st.form_submit_button("Create user", type="primary"):
                if not name.strip():
                    st.error("Give the user a name.")
                else:
                    uid = db.create_user(agency_id, name, role, email)
                    for c in chosen:
                        db.assign_user(uid, rooms[rlabels.index(c)]["id"])
                    st.success(f"Created {name.strip()}.")
                    st.rerun()

    st.markdown("**What each role can do**")
    st.dataframe(
        pd.DataFrame([{
            "Role": db.ROLES[r],
            "Can do": ", ".join(sorted(db.CAPS[r])),
        } for r in [db.ROLE_USER, db.ROLE_TEAM_ADMIN, db.ROLE_APP_ADMIN]]),
        width="stretch", hide_index=True,
    )

    people = db.list_users(agency_id)
    if not people:
        st.info("No users yet.")
        return

    st.divider()
    st.dataframe(
        pd.DataFrame([{
            "Name": u["name"], "Email": u["email"] or "—",
            "Role": db.ROLES.get(u["role"], u["role"]),
            "Storerooms": ", ".join(s["name"] for s in db.storerooms_for_user(u["id"]))
                          or "— none —",
        } for u in people]),
        width="stretch", hide_index=True,
    )

    st.markdown("**Edit a user**")
    labels = [f"{u['name']} — {db.ROLES.get(u['role'], u['role'])}" for u in people]
    pick = st.selectbox("User", labels, key="us_edit")
    user = people[labels.index(pick)]

    c1, c2 = st.columns(2)
    with c1:
        roles = list(db.ROLES.keys())
        newrole = st.selectbox("Role", roles, index=roles.index(user["role"]),
                               format_func=lambda r: db.ROLES[r], key=f"ur_{user['id']}")
        st.caption(ROLE_HELP.get(newrole, ""))
        if st.button("Save role", key=f"urb_{user['id']}"):
            db.update_user(user["id"], role=newrole)
            st.success("Saved.")
            st.rerun()
    with c2:
        rooms = db.list_storerooms(agency_id)
        mine = {s["id"] for s in db.storerooms_for_user(user["id"])}
        rlabels = [r["name"] for r in rooms]
        chosen = st.multiselect(
            "Storerooms", rlabels,
            default=[r["name"] for r in rooms if r["id"] in mine],
            key=f"ust_{user['id']}",
        )
        if st.button("Save storerooms", key=f"ustb_{user['id']}"):
            want = {rooms[rlabels.index(c)]["id"] for c in chosen}
            for r in rooms:
                if r["id"] in want and r["id"] not in mine:
                    db.assign_user(user["id"], r["id"])
                elif r["id"] not in want and r["id"] in mine:
                    db.unassign_user(user["id"], r["id"])
            st.success("Saved.")
            st.rerun()

    if st.button(f"🗑 Delete {user['name']}", key=f"usd_{user['id']}"):
        db.delete_user(user["id"])
        st.success("Deleted.")
        st.rerun()
