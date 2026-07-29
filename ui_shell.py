"""Landing page and shared chrome.

The app opens on a landing page — quick action cards, then an inventory
overview — rather than dropping straight into a wall of tabs. Picking an action
navigates to that one screen, so a nurse sees a short list of choices and then
one job at a time.

Navigation goes through the `nav` query parameter rather than session state, so
the cards can be real anchors. That keeps them styleable as cards (icon above
label, which a Streamlit button cannot do) and makes the back link and the
browser's own back button behave the same way.

Every HTML string here is emitted on ONE line. Markdown treats an indented line
as a code block, so a pretty-printed SVG comes out as visible source text rather
than a drawing.
"""

import math

import streamlit as st

import store_db as db

ACCENT = "#A8741A"

_SVG = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="{w}" stroke-linecap="round" stroke-linejoin="round">{d}</svg>')

_ICON_CART = _SVG.format(w="1.7", d=(
    '<circle cx="9" cy="20" r="1.4"/><circle cx="18" cy="20" r="1.4"/>'
    '<path d="M2 3h2.2l2.4 11.2a2 2 0 0 0 2 1.6h8.6a2 2 0 0 0 2-1.6L21 7H5"/>'))
_ICON_PLUS = _SVG.format(w="2", d='<path d="M12 5v14M5 12h14"/>')
_ICON_CLIP = _SVG.format(w="1.7", d=(
    '<rect x="5" y="4" width="14" height="17" rx="2"/>'
    '<path d="M9 3h6v3H9zM9 11h6M9 15h4"/>'))
_ICON_SWAP = _SVG.format(w="1.8", d='<path d="M4 8h15l-3.5-3.5M20 16H5l3.5 3.5"/>')
_ICON_BIN = _SVG.format(w="1.7", d=(
    '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6"/>'))
_ICON_WAREHOUSE = _SVG.format(w="1.7", d='<path d="M3 21V9l9-5 9 5v12M8 21v-6h8v6"/>')

# nav key -> (label, icon, capability required)
QUICK_ACTIONS = [
    ("withdraw", "Withdraw", _ICON_CART, "withdraw"),
    ("stock_up", "Stock up", _ICON_PLUS, "add_stock"),
    ("stock_take", "Stock take", _ICON_CLIP, "stock_take"),
    ("transfer", "Transfer", _ICON_SWAP, "transfer"),
    ("dispose", "Dispose", _ICON_BIN, "dispose"),
]

CSS = """
<style>
  /* Clear Streamlit's floating toolbar, or the storeroom name is clipped. */
  .block-container { padding-top: 3.1rem; padding-bottom: 3rem; max-width: 860px; }

  .store-name {
    font-size: 1.06rem; font-weight: 700; color: #6B7280;
    letter-spacing: .01em; margin: 0 0 .2rem;
  }

  .sec-title {
    font-size: 1.95rem; font-weight: 800; letter-spacing: -.02em;
    margin: 1.2rem 0 .9rem; line-height: 1.1; color: #14161A;
  }

  .qa-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 14px; }
  .qa-card {
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: 12px; padding: 30px 12px;
    background: #FAFAFA; border: 1px solid #ECECEC; border-radius: 16px;
    text-decoration: none !important; color: inherit !important;
    transition: background .12s, border-color .12s;
  }
  .qa-card:hover { background: #F3F3F3; border-color: #DDD; }
  .qa-card .ic { display: block; width: 34px; height: 34px; color: ACCENTCOLOR; }
  .qa-card .ic svg { width: 100%; height: 100%; display: block; }
  .qa-card .lb { font-size: 1.12rem; font-weight: 600; color: #14161A; }

  .ov-card {
    background: #FAFAFA; border: 1px solid #ECECEC; border-radius: 16px;
    padding: 20px 22px;
  }
  .ov-head {
    display: flex; align-items: center; gap: 9px;
    font-size: 1.02rem; color: #6B7280; margin-bottom: 4px;
  }
  .ov-head .ic { display: block; width: 19px; height: 19px; color: #6B7280; }
  .ov-head .ic svg { width: 100%; height: 100%; display: block; }
  .ov-body { display: flex; align-items: center; gap: 18px; flex-wrap: wrap; }
  .ov-legend { flex: 1 1 168px; min-width: 168px; }
  .ov-row {
    display: flex; align-items: center; gap: 11px;
    padding: 8px 0; font-size: 1.05rem; color: #14161A;
  }
  .ov-row .dot { width: 11px; height: 11px; border-radius: 50%; flex-shrink: 0; }
  .ov-row .nm { flex: 1; }
  .ov-row .vl { font-weight: 700; }

  .wide-link {
    display: block; text-align: center; padding: 15px;
    border: 1px solid #E3E3E3; border-radius: 14px; margin-top: 14px;
    text-decoration: none !important; color: #14161A !important;
    font-size: 1.04rem; font-weight: 600; background: #fff;
  }
  .wide-link:hover { background: #FAFAFA; }

  .back-link {
    display: inline-block; margin-bottom: .4rem; text-decoration: none !important;
    color: #6B7280 !important; font-size: .96rem; font-weight: 600;
  }
  .back-link:hover { color: #14161A !important; }

  .chip-row { display: flex; gap: 9px; flex-wrap: wrap; margin-top: 14px; }
  .chip {
    padding: 9px 15px; border: 1px solid #E3E3E3; border-radius: 999px;
    background: #fff; font-size: .95rem; font-weight: 600;
    text-decoration: none !important; color: #14161A !important;
  }
  .chip:hover { background: #FAFAFA; }
  .chip.warn { border-color: #F3C6C1; color: #A3231A !important; background: #FDF4F3; }

  @media (max-width: 430px) {
    .sec-title { font-size: 1.65rem; }
    .qa-card { padding: 24px 8px; }
  }
</style>
""".replace("ACCENTCOLOR", ACCENT)


def inject_css():
    st.markdown(CSS, unsafe_allow_html=True)


def _link(nav, inner, cls):
    # target=_self keeps navigation inside the Streamlit session.
    return f'<a class="{cls}" href="?nav={nav}" target="_self">{inner}</a>'


def back_link(label="← Back"):
    st.markdown(_link("", label, "back-link"), unsafe_allow_html=True)


def donut(in_stock, out_of_stock, size=132):
    """Ring showing how many assigned items actually have stock."""
    total = in_stock + out_of_stock
    r, sw = 62, 22
    circ = 2 * math.pi * r
    dash = circ * ((in_stock / total) if total else 0)
    centre = "—" if total == 0 else str(in_stock)
    fs = 30 if total == 0 else 34
    font = "-apple-system,Segoe UI,Roboto,sans-serif"

    arc = ""
    if total:
        arc = (f'<circle cx="80" cy="80" r="{r}" fill="none" stroke="#12B76A" '
               f'stroke-width="{sw}" stroke-dasharray="{dash:.2f} {circ - dash:.2f}" '
               f'stroke-linecap="round" transform="rotate(-90 80 80)"/>')

    return (
        f'<svg viewBox="0 0 160 160" width="{size}" height="{size}">'
        f'<circle cx="80" cy="80" r="{r}" fill="none" stroke="#E6E6E6" stroke-width="{sw}"/>'
        f'{arc}'
        f'<text x="80" y="78" text-anchor="middle" font-size="{fs}" font-weight="800" '
        f'fill="#14161A" font-family="{font}">{centre}</text>'
        f'<text x="80" y="99" text-anchor="middle" font-size="13" fill="#6B7280" '
        f'font-family="{font}">in stock</text>'
        f'</svg>'
    )


def _row(colour, name, value):
    return (f'<div class="ov-row"><span class="dot" style="background:{colour}"></span>'
            f'<span class="nm">{name}</span><span class="vl">{value}</span></div>')


def store_header(room):
    """The storeroom name leads the page."""
    name = room["name"] if room else "Ward Store"
    st.markdown(f'<div class="store-name">{name}</div>', unsafe_allow_html=True)


def _get_started(user):
    """Shown when there is no storeroom to act on.

    Without this the page dead-ends: the Admin link used to sit below the
    inventory overview, which is only reached when a storeroom exists — so a
    fresh App Admin had no way to create their first one.
    """
    can_admin = db.can(user["role"], "manage_storerooms")
    st.markdown('<div class="sec-title">Get started</div>', unsafe_allow_html=True)

    if not can_admin:
        st.warning(
            "You are not assigned to a storeroom yet. An App Admin needs to "
            "assign you before you can add or withdraw stock."
        )
        return

    st.markdown(
        _link("admin", "Open Admin  ↗", "wide-link"),
        unsafe_allow_html=True,
    )
    st.caption(
        "Three steps: create a storeroom, build the master inventory list "
        "(scan, type or upload a CSV), then assign items to the storeroom."
    )
    st.caption(
        "Admin also has a one-click demo setup if you would rather look around "
        "with data already in place."
    )


def landing(agency_id, room, user):
    """Quick actions, then the inventory overview."""
    if room is None:
        _get_started(user)
        return

    st.markdown('<div class="sec-title">Quick actions</div>', unsafe_allow_html=True)
    cards = "".join(
        _link(nav, f'<span class="ic">{icon}</span><span class="lb">{label}</span>',
              "qa-card")
        for nav, label, icon, cap in QUICK_ACTIONS
        if db.can(user["role"], cap)
    )
    st.markdown(f'<div class="qa-grid">{cards}</div>', unsafe_allow_html=True)

    st.markdown('<div class="sec-title">Inventory overview</div>', unsafe_allow_html=True)

    rows = db.storeroom_items(room["id"])
    in_stock = sum(1 for r in rows if r["on_hand"] > 0)
    out_stock = sum(1 for r in rows if r["on_hand"] <= 0)
    low = [r for r in rows if r["below_min"]]
    exp = db.expiring(room["id"], within_days=30)
    expired = [e for e in exp if e["days_left"] < 0]

    card = (
        '<div class="ov-card">'
        f'<div class="ov-head"><span class="ic">{_ICON_WAREHOUSE}</span>'
        '<span>Stock status</span></div>'
        '<div class="ov-body">'
        f'<div>{donut(in_stock, out_stock)}</div>'
        '<div class="ov-legend">'
        + _row("#12B76A", "In stock", in_stock)
        + _row("#B6BAC3", "Out of stock", out_stock)
        + _row("#F79009", "Below minimum", len(low))
        + _row("#F04438", "Expiring ≤30 days", len(exp))
        + '</div></div></div>'
        + _link("inventory", "View available stock  ↗", "wide-link")
    )
    st.markdown(card, unsafe_allow_html=True)

    chips = [
        _link("activity", "Activity", "chip"),
        _link("low_stock", f"Low stock · {len(low)}", "chip warn" if low else "chip"),
        _link("expiry", f"Expiry · {len(exp)}", "chip warn" if expired else "chip"),
    ]
    if db.can(user["role"], "manage_storerooms"):
        chips.append(_link("admin", "Admin", "chip"))
    st.markdown(f'<div class="chip-row">{"".join(chips)}</div>', unsafe_allow_html=True)
