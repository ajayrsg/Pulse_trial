"""Ward store inventory — batch-scanning stock control.

Run:  streamlit run inventory_app.py

Two audiences:

  Day-to-day (phone / tablet) — Add Stock and Withdraw are driven by the batch
  scanner: tip items under the camera and everything is counted at once, with no
  per-item tap and no confirm step. Plus Activity, Inventory, Low Stock, Expiry,
  and — for Team Admins — Transfer and Dispose.

  Admin (desktop) — build the master inventory list, create storerooms, assign
  items and people to them, and set the webhook a scheduler calls.

There is NO authentication. The sidebar simply picks who you are acting as,
which is enough to demonstrate the three roles but is not a login. Anyone with
the URL can act as anyone, so this must not hold real data as it stands.
"""

import streamlit as st

import admin_ui
import ops_ui
import store_db as db

st.set_page_config(page_title="Ward Store", layout="wide")

AGENCY_ID = db.ensure_agency()


def _bootstrap_notice():
    """First run has no users, so nobody could act as an App Admin."""
    st.title("🏥 Ward Store")
    st.info(
        "Nothing is set up yet. Create the first **App Admin** to begin — "
        "they build the master inventory list, the storerooms, and everyone else."
    )
    with st.form("first_admin"):
        name = st.text_input("Your name", value="")
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
            _seed_demo()
            st.rerun()


def _seed_demo():
    from datetime import date, timedelta

    a = AGENCY_ID
    r1 = db.create_storeroom(a, "Ward 5A Store")
    r2 = db.create_storeroom(a, "Ward 7B Store")

    admin = db.create_user(a, "Admin (demo)", db.ROLE_APP_ADMIN)
    lead = db.create_user(a, "Team Admin (demo)", db.ROLE_TEAM_ADMIN)
    nurse = db.create_user(a, "Nurse (demo)", db.ROLE_USER)
    for u in (admin, lead, nurse):
        db.assign_user(u, r1)
    db.assign_user(admin, r2)
    db.assign_user(lead, r2)

    soon = (date.today() + timedelta(days=20)).isoformat()
    later = (date.today() + timedelta(days=400)).isoformat()
    gone = (date.today() - timedelta(days=7)).isoformat()

    spec = [
        ("Syringe 5ml", "5012345678900", "EA", 20, [(30, soon), (25, later)]),
        ("Nitrile glove M", "5012345678901", "BX", 5, [(2, None)]),
        ("Gauze pad", "5012345678902", "PK", 10, [(12, later), (4, gone)]),
        ("Saline 0.9% 500ml", "5012345678903", "BO", 8, [(9, soon)]),
        ("Micropore tape", "5012345678904", "RL", 4, [(1, None)]),
    ]
    for name, bc, u, minq, lots in spec:
        iid = db.create_item(a, name, bc, u)
        db.assign_item(r1, iid, min_qty=minq)
        for qty, exp in lots:
            db.stock_in(r1, iid, qty, exp, user_id=nurse)
        db.assign_item(r2, iid, min_qty=max(1, minq // 2))

    db.stock_in(r2, db.item_by_barcode(a, "5012345678900")["id"], 5, soon, user_id=nurse)
    db.withdraw(r1, db.item_by_barcode(a, "5012345678900")["id"], 6, user_id=nurse)


people = db.list_users(AGENCY_ID)
if not people:
    _bootstrap_notice()
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
    rooms = db.list_storerooms(AGENCY_ID) if is_app_admin else db.storerooms_for_user(user["id"])

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
# Header — the storeroom name leads, per the spec
# --------------------------------------------------------------------------
if room:
    st.title(f"🏥 {room['name']}")
    counts = db.storeroom_items(room["id"])
    low = [c for c in counts if c["below_min"]]
    exp = db.expiring(room["id"], within_days=ops_ui.EXPIRY_WARN_DAYS)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Items", len(counts))
    m2.metric("Units on hand", sum(c["on_hand"] for c in counts))
    m3.metric("Below minimum", len(low))
    m4.metric(f"Expiring ≤{ops_ui.EXPIRY_WARN_DAYS}d", len(exp))
else:
    st.title("🏥 Ward Store")
    if user["role"] == db.ROLE_APP_ADMIN:
        st.info("No storerooms yet — create one under **Admin → Storerooms**.")
    else:
        st.warning("You are not assigned to any storeroom. Ask an App Admin to assign you.")

# --------------------------------------------------------------------------
# Sections, gated by role
# --------------------------------------------------------------------------
SECTIONS = [
    ("add_stock", "➕ Add Stock", ops_ui.add_stock),
    ("withdraw", "➖ Withdraw", ops_ui.withdraw),
    ("transfer", "🔁 Transfer", ops_ui.transfer),
    ("dispose", "🗑 Dispose", ops_ui.dispose),
    ("activity", "🧾 Activity", ops_ui.activity),
    ("inventory", "📦 Inventory", ops_ui.inventory),
    ("low_stock", "⚠️ Low Stock", ops_ui.low_stock),
    ("expiry", "⏰ Expiry", ops_ui.expiry),
]

allowed = [(cap, label, fn) for cap, label, fn in SECTIONS if db.can(user["role"], cap)]
admin_caps = db.can(user["role"], "manage_storerooms")

labels = [label for _, label, _ in allowed]
if admin_caps:
    labels = labels + ["🛠 Admin"]

if room or admin_caps:
    tabs = st.tabs(labels)
    for i, (cap, label, fn) in enumerate(allowed):
        with tabs[i]:
            if room is None:
                st.info("Select a storeroom first.")
            else:
                fn(AGENCY_ID, room, user)

    if admin_caps:
        with tabs[len(allowed)]:
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
