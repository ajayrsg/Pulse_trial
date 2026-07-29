"""Ward store inventory — batch-scanning stock control.

Run:  streamlit run inventory_app.py

Opens on a landing page: quick action cards, then an inventory overview. Picking
an action navigates to that single screen, so a nurse sees a short list of
choices and then one job at a time rather than a row of tabs.

  Day-to-day (phone / tablet) — Stock up and Withdraw are driven by the batch
  scanner: tip items under the camera and everything is counted at once, with no
  per-item tap and no confirm step. Team Admins also get Transfer, Dispose and
  Stock take.

  Admin (desktop) — build the master inventory list, create storerooms, assign
  items and people to them, and set the webhook a scheduler calls.

There is NO authentication. The sidebar simply picks who you are acting as,
which is enough to demonstrate the three roles but is not a login. Anyone with
the URL can act as anyone, so this must not hold real data as it stands.
"""

import streamlit as st

import admin_ui
import demo
import ops_ui
import store_db as db
import ui_shell

st.set_page_config(page_title="Ward Store", layout="centered",
                   initial_sidebar_state="collapsed")

AGENCY_ID = db.ensure_agency()
ui_shell.inject_css()

# nav key -> (capability, title, renderer)
SCREENS = {
    "withdraw":   ("withdraw",   "Withdraw",    ops_ui.withdraw),
    "stock_up":   ("add_stock",  "Stock up",    ops_ui.add_stock),
    "stock_take": ("stock_take", "Stock take",  ops_ui.stock_take),
    "transfer":   ("transfer",   "Transfer",    ops_ui.transfer),
    "dispose":    ("dispose",    "Dispose",     ops_ui.dispose),
    "activity":   ("activity",   "Activity",    ops_ui.activity),
    "inventory":  ("inventory",  "Inventory",   ops_ui.inventory),
    "low_stock":  ("low_stock",  "Low stock",   ops_ui.low_stock),
    "expiry":     ("expiry",     "Expiry",      ops_ui.expiry),
}


def _bootstrap():
    """First run has no users, so nobody could act as an App Admin."""
    st.title("🏥 Ward Store")
    st.info(
        "Nothing is set up yet. Create the first **App Admin** to begin — "
        "they build the master inventory list, the storerooms, and everyone else."
    )
    with st.form("first_admin"):
        name = st.text_input("Your name")
        email = st.text_input("Email (optional)")
        if st.form_submit_button("Create App Admin", type="primary"):
            if not name.strip():
                st.error("Enter a name.")
            else:
                db.create_user(AGENCY_ID, name, db.ROLE_APP_ADMIN, email)
                st.rerun()

    with st.expander("…or load a small demo setup to try it out"):
        st.caption(
            "Creates two storerooms, three users covering all three roles, and a "
            "handful of items with stock — enough to exercise every screen."
        )
        if st.button("Load demo data"):
            demo.seed(AGENCY_ID)
            st.rerun()


people = db.list_users(AGENCY_ID)
if not people:
    _bootstrap()
    st.stop()

# --------------------------------------------------------------------------
# Sidebar: who am I, and which storeroom
# --------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### Acting as")
    ulabels = [f"{u['name']} — {db.ROLES.get(u['role'], u['role'])}" for u in people]
    upick = st.selectbox("User", ulabels, key="cur_user", label_visibility="collapsed")
    user = people[ulabels.index(upick)]
    st.caption("No login — this switcher stands in for authentication.")

    is_app_admin = user["role"] == db.ROLE_APP_ADMIN
    rooms = (db.list_storerooms(AGENCY_ID) if is_app_admin
             else db.storerooms_for_user(user["id"]))

    st.divider()
    st.markdown("### Storeroom")
    room = None
    if rooms:
        rlabels = [r["name"] for r in rooms]
        rpick = st.selectbox("Storeroom", rlabels, key="cur_room",
                             label_visibility="collapsed")
        room = rooms[rlabels.index(rpick)]
        if is_app_admin:
            st.caption("App Admins see every storeroom.")
    else:
        st.caption("None assigned to you.")

    st.divider()
    agency = db.get_agency(AGENCY_ID)
    st.caption(f"Agency: {agency['name'] if agency else '—'}")

# --------------------------------------------------------------------------
# Route on the nav query parameter
# --------------------------------------------------------------------------
nav = (st.query_params.get("nav") or "").strip()

# Header: the storeroom name leads, per the spec.
ui_shell.store_header(room)
if not room and not st.session_state.get("_warned"):
    if user["role"] == db.ROLE_APP_ADMIN:
        st.info("No storerooms yet — open **Admin** below to create one.")
    else:
        st.warning("You are not assigned to any storeroom. Ask an App Admin to assign you.")

if nav == "admin":
    if not db.can(user["role"], "manage_storerooms"):
        ui_shell.back_link()
        st.error("Your role cannot manage storerooms, inventory or users.")
    else:
        ui_shell.back_link()
        st.caption("Best used on a desktop screen.")
        a_rooms, a_items, a_users = st.tabs(
            ["🏬 Storerooms", "📋 Master Inventory", "👥 Users"]
        )
        with a_rooms:
            admin_ui.storerooms(AGENCY_ID)
        with a_items:
            admin_ui.master_inventory(AGENCY_ID)
        with a_users:
            admin_ui.users(AGENCY_ID)

elif nav in SCREENS:
    cap, title, render = SCREENS[nav]
    ui_shell.back_link()
    if not db.can(user["role"], cap):
        st.error(f"**{title}** is not available to a {db.ROLES.get(user['role'])}.")
    elif room is None:
        st.info("Select a storeroom in the sidebar first.")
    else:
        render(AGENCY_ID, room, user)

else:
    ui_shell.landing(AGENCY_ID, room, user)
