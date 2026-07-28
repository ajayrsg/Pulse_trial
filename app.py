"""Step 3 + 4 (+ live view): Streamlit dashboard with audit trail.

Run:  streamlit run app.py

Modes:
  - Snapshot: click Take Photo (browser camera) -> Analyze & record.
  - Live:     auto-capture a frame every few seconds and update counts, using
              multi-frame agreement (a count is only "stable" when consecutive
              frames agree). Near-live, not true per-frame video — the vision
              API is request/response, seconds + token cost per call.

Tabs (always visible):
  - Inventory:    latest count per catalog item vs Min Stock (red if below)
  - Expiry:       detected expiry dates, soonest first (red within 30 days)
  - Transactions: per-item count changes between snapshots
  - Audit Trail:  every image + the raw model response, for manual accuracy checks

Every analyzed frame (snapshot OR live) is written to the audit trail, so real
accuracy can always be checked by eye later.
"""

import json
import os
import statistics
import time
from collections import deque
from datetime import datetime, date

import pandas as pd
import streamlit as st

import capture
import db
import vision
from audit import save_audit_record
from poc_config import catalog_names, min_stock_map

st.set_page_config(page_title="Ward Storeroom POC", layout="wide")

db.init_db()

EXPIRY_WARN_DAYS = 30

# Session state for live mode
st.session_state.setdefault("live_on", False)
st.session_state.setdefault("live_buffer", deque(maxlen=3))
st.session_state.setdefault("live_api_calls", 0)
st.session_state.setdefault("live_interval", 5)
st.session_state.setdefault("live_last_frame", None)
st.session_state.setdefault("live_last_expiry", [])
st.session_state.setdefault("live_error", None)

_do_live_rerun = False  # set True at the end of a live cycle to schedule the next


# --------------------------------------------------------------------------
# Analysis helpers
# --------------------------------------------------------------------------
def analyze_and_audit(image_path):
    """Run Step 2 on an image and write the audit record. No DB write.

    Returns {"parsed", "raw_text", "error", "audit_path", "ts"}.
    """
    parsed, raw_text, error = None, "", None
    try:
        result = vision.analyze_image(image_path)
        parsed = result["parsed"]
        raw_text = result["raw_text"]
        error = result["error"]
    except Exception as e:  # noqa: BLE001 - surface any API/auth failure to the UI
        error = f"{type(e).__name__}: {e}"

    ts_iso = datetime.now().isoformat(timespec="seconds")
    audit_path = save_audit_record(
        ts_iso, image_path, raw_text, parsed, error, vision.MODEL
    )
    return {
        "parsed": parsed,
        "raw_text": raw_text,
        "error": error,
        "audit_path": audit_path,
        "ts": ts_iso,
    }


def catalog_counts_from_parsed(parsed):
    """Map a parsed result to {catalog_item: count} (matched items only)."""
    from poc_config import match_to_catalog

    counts = {name: 0 for name in catalog_names()}
    if not isinstance(parsed, dict):
        return counts
    for item in parsed.get("items", []):
        if not isinstance(item, dict):
            continue
        matched = match_to_catalog(item.get("name"))
        if not matched:
            continue
        try:
            counts[matched] += int(item.get("count"))
        except (TypeError, ValueError):
            pass
    return counts


def expiry_strings_from_parsed(parsed):
    """Flat list of (item_label, raw_expiry_string) from a parsed result."""
    from poc_config import match_to_catalog

    out = []
    if not isinstance(parsed, dict):
        return out
    for item in parsed.get("items", []):
        if not isinstance(item, dict):
            continue
        label = match_to_catalog(item.get("name")) or item.get("name") or "—"
        for raw in item.get("expiry_dates_found") or []:
            out.append((label, raw))
    return out


def _parse_expiry(raw):
    dt = pd.to_datetime(raw, errors="coerce")
    return None if pd.isna(dt) else dt.date()


def _highlight_below_min(row):
    color = "background-color: #f8d7da" if row["Below min"] else ""
    return [color] * len(row)


def _highlight_expiry(row):
    color = "background-color: #f8d7da" if row["_flag"] in ("expired", "soon") else ""
    return [color] * len(row)


# --------------------------------------------------------------------------
# Header + mode selector
# --------------------------------------------------------------------------
st.title("🏥 Ward Storeroom Inventory — Camera POC")
st.caption(
    "Rough internal proof-of-concept. Counts and expiry reads are AI estimates — "
    "check the Audit Trail tab, and the accuracy report, before trusting any number."
)

mode = st.radio(
    "Capture mode", ["📸 Snapshot", "🎥 Live count"], horizontal=True,
    help="Live mode auto-captures every few seconds and consumes API tokens each tick.",
)


# --------------------------------------------------------------------------
# Snapshot mode
# --------------------------------------------------------------------------
if mode == "📸 Snapshot":
    st.session_state["live_on"] = False
    st.markdown("### New snapshot")
    st.caption("Point the camera at the shelf, click **Take Photo**, then **Analyze & record**.")
    photo = st.camera_input("Webcam", label_visibility="collapsed")
    if photo is not None and st.button("Analyze & record this snapshot", type="primary"):
        image_path = capture.save_bytes(photo.getvalue())
        st.image(image_path, caption=os.path.basename(image_path), width=480)
        with st.spinner(f"Analyzing with {vision.MODEL}…"):
            res = analyze_and_audit(image_path)
        db.record_snapshot(res["ts"], image_path, res["audit_path"], res["parsed"], res["error"])
        if res["parsed"] is not None:
            st.success("Snapshot analyzed and recorded.")
            if res["parsed"].get("notes"):
                st.info(f"Model notes: {res['parsed']['notes']}")
        else:
            st.error(
                "Image + raw response saved to the audit trail, but analysis did not "
                f"return valid data. Details: {res['error']}"
            )


# --------------------------------------------------------------------------
# Live mode
# --------------------------------------------------------------------------
if mode == "🎥 Live count":
    st.markdown("### Live count")
    st.caption(
        "Auto-captures a frame on a timer and updates counts. This is **near-live** "
        "(a few seconds per cycle), and each cycle calls the API. A count is shown as "
        "**stable** only when the last few frames agree — disagreement is surfaced, "
        "not hidden."
    )

    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        interval = st.number_input(
            "Seconds between frames", min_value=3, max_value=30,
            value=int(st.session_state["live_interval"]), step=1,
        )
        st.session_state["live_interval"] = interval
    with c2:
        k = st.number_input("Frames to agree", min_value=1, max_value=7, value=3, step=1)
        if st.session_state["live_buffer"].maxlen != k:
            st.session_state["live_buffer"] = deque(
                st.session_state["live_buffer"], maxlen=k
            )
    with c3:
        log_each = st.checkbox(
            "Also log each reading to history (Inventory/Transactions)",
            value=False,
            help="Off by default to avoid flooding the transaction log every few seconds.",
        )

    b1, b2 = st.columns(2)
    if b1.button("▶ Start", type="primary", disabled=st.session_state["live_on"]):
        st.session_state["live_on"] = True
        st.session_state["live_buffer"].clear()
        st.session_state["live_error"] = None
    if b2.button("⏹ Stop", disabled=not st.session_state["live_on"]):
        st.session_state["live_on"] = False

    # One capture+analyze cycle per rerun while live is on.
    if st.session_state["live_on"]:
        try:
            data = capture.grab_jpeg_bytes()
            image_path = capture.save_bytes(data, ext=".jpg")
            res = analyze_and_audit(image_path)
            st.session_state["live_api_calls"] += 1
            st.session_state["live_last_frame"] = image_path
            if res["parsed"] is not None:
                st.session_state["live_buffer"].append(
                    catalog_counts_from_parsed(res["parsed"])
                )
                st.session_state["live_last_expiry"] = expiry_strings_from_parsed(
                    res["parsed"]
                )
                st.session_state["live_error"] = res["error"]
                if log_each:
                    db.record_snapshot(
                        res["ts"], image_path, res["audit_path"],
                        res["parsed"], res["error"],
                    )
            else:
                st.session_state["live_error"] = res["error"]
            _do_live_rerun = True
        except RuntimeError as e:
            st.session_state["live_on"] = False
            st.session_state["live_error"] = str(e)

    status = "🟢 running" if st.session_state["live_on"] else "⚪ stopped"
    st.write(f"**Status:** {status}  |  API calls this session: {st.session_state['live_api_calls']}")
    if st.session_state["live_error"]:
        st.warning(f"Last cycle note: {st.session_state['live_error']}")

    lcol, rcol = st.columns([1, 1])
    with lcol:
        if st.session_state["live_last_frame"] and os.path.exists(st.session_state["live_last_frame"]):
            st.image(st.session_state["live_last_frame"], caption="Latest frame", width="stretch")
    with rcol:
        buf = list(st.session_state["live_buffer"])
        if buf:
            mins = min_stock_map()
            rows = []
            for name in catalog_names():
                series = [frame.get(name, 0) for frame in buf]
                stable = int(statistics.median(series))
                agree = len(set(series)) == 1
                rows.append(
                    {
                        "Item": name,
                        "Recent frames": ", ".join(str(s) for s in series),
                        "Stable count": stable,
                        "Frames agree": "✓" if agree else "✗",
                        "Min Stock": mins.get(name, 0),
                        "Below min": stable < mins.get(name, 0),
                    }
                )
            df = pd.DataFrame(rows)
            st.dataframe(
                df.style.apply(_highlight_below_min, axis=1),
                width="stretch", hide_index=True,
            )
            st.caption(
                f"Stable count = median of the last {len(buf)} frame(s). "
                "✗ under 'Frames agree' means the model gave different counts across "
                "frames — treat those as unreliable."
            )
        else:
            st.info("Waiting for the first analyzed frame…")

        if st.session_state["live_last_expiry"]:
            st.markdown("**Expiry strings in the latest frame** (noisy in live mode):")
            st.dataframe(
                pd.DataFrame(st.session_state["live_last_expiry"], columns=["Item", "Expiry (raw)"]),
                width="stretch", hide_index=True,
            )


# Latest recorded snapshot caption
latest = db.latest_parsed_snapshot()
if latest:
    st.caption(
        f"Latest recorded snapshot: {latest['ts']} "
        f"(image: {os.path.basename(latest['image_path'] or '—')})"
    )
else:
    st.caption("No recorded snapshots yet.")


# --------------------------------------------------------------------------
# Data tabs
# --------------------------------------------------------------------------
tab_inv, tab_exp, tab_txn, tab_audit = st.tabs(
    ["📦 Inventory", "⏰ Expiry", "🔁 Transactions", "🔍 Audit Trail"]
)

with tab_inv:
    st.subheader("Latest recorded count per item vs. Min Stock")
    mins = min_stock_map()
    latest = db.latest_parsed_snapshot()
    if not latest:
        st.info("No recorded snapshot yet.")
    else:
        counts = db.item_counts_for_snapshot(latest["id"])
        by_item = {}
        conf_rank = {"high": 3, "medium": 2, "low": 1}
        for r in counts:
            item = r["matched_item"]
            if not item:
                continue
            entry = by_item.setdefault(item, {"count": 0, "confidence": "high"})
            entry["count"] += r["count"]
            cur = conf_rank.get((r["confidence"] or "").lower(), 1)
            if cur < conf_rank.get(entry["confidence"], 3):
                entry["confidence"] = (r["confidence"] or "low").lower()

        rows = []
        for name in catalog_names():
            count = by_item.get(name, {}).get("count", 0)
            confidence = by_item.get(name, {}).get("confidence", "—")
            min_stock = mins.get(name, 0)
            rows.append(
                {
                    "Item": name,
                    "Count": count,
                    "Min Stock": min_stock,
                    "Confidence": confidence,
                    "Below min": count < min_stock,
                }
            )
        df = pd.DataFrame(rows)
        st.dataframe(
            df.style.apply(_highlight_below_min, axis=1),
            width="stretch", hide_index=True,
        )
        st.caption(f"From snapshot {latest['ts']}. Red = below Min Stock.")
        unmatched = sorted(
            {r["detected_name"] for r in counts if not r["matched_item"] and r["detected_name"]}
        )
        if unmatched:
            st.caption("Detected but not in catalog: " + ", ".join(unmatched))

with tab_exp:
    st.subheader("Detected expiry dates — soonest first")
    latest = db.latest_parsed_snapshot()
    if not latest:
        st.info("No recorded snapshot yet.")
    else:
        counts = db.item_counts_for_snapshot(latest["id"])
        today = date.today()
        parsed_rows, unparsed_rows = [], []
        for r in counts:
            item_label = r["matched_item"] or r["detected_name"] or "—"
            try:
                expiries = json.loads(r["expiry_dates_json"] or "[]")
            except json.JSONDecodeError:
                expiries = []
            for raw in expiries:
                d = _parse_expiry(raw)
                if d is None:
                    unparsed_rows.append({"Item": item_label, "Expiry (raw)": raw})
                    continue
                days = (d - today).days
                flag = "expired" if days < 0 else ("soon" if days <= EXPIRY_WARN_DAYS else "ok")
                parsed_rows.append(
                    {"Item": item_label, "Expiry": d.isoformat(), "Days left": days,
                     "_flag": flag, "_sort": d}
                )
        if parsed_rows:
            df = pd.DataFrame(sorted(parsed_rows, key=lambda x: x["_sort"]))
            show = df.drop(columns=["_sort"])
            st.dataframe(
                show.style.apply(_highlight_expiry, axis=1),
                width="stretch", hide_index=True,
                column_config={"_flag": None},
            )
            st.caption(f"Red = expired or within {EXPIRY_WARN_DAYS} days.")
        else:
            st.info("No parseable expiry dates in the latest snapshot.")
        if unparsed_rows:
            st.caption("Detected expiry strings that couldn't be parsed as dates:")
            st.dataframe(pd.DataFrame(unparsed_rows), width="stretch", hide_index=True)

with tab_txn:
    st.subheader("Count changes between snapshots")
    txns = db.all_transactions()
    if not txns:
        st.info("No transactions yet — they appear once you have two or more snapshots.")
    else:
        df = pd.DataFrame(txns)[["ts", "item_name", "count_before", "count_after", "delta"]]
        df.columns = ["Timestamp", "Item", "Count before", "Count after", "Delta"]
        st.dataframe(df, width="stretch", hide_index=True)
        st.caption(
            "Each row is a per-item change vs. the previous recorded snapshot. "
            "Deltas are only as reliable as the AI counts that produced them."
        )

with tab_audit:
    st.subheader("Every capture + the raw model response")
    st.caption(
        "The ground truth for measuring accuracy. Open the image and read what the AI "
        "actually returned, then judge whether the count/expiry were right. Run "
        "`python measure_accuracy.py` for a scored report."
    )
    snapshots = db.all_snapshots()
    if not snapshots:
        st.info("No snapshots recorded yet.")
    else:
        for s in snapshots[:50]:
            status = "✅ parsed" if s["parsed_ok"] else "⚠️ not parsed"
            header = f"{s['ts']} — {os.path.basename(s['image_path'] or '—')} ({status})"
            with st.expander(header):
                cols = st.columns([1, 1])
                with cols[0]:
                    if s["image_path"] and os.path.exists(s["image_path"]):
                        st.image(s["image_path"], width="stretch")
                    else:
                        st.caption("Image file not found.")
                with cols[1]:
                    if s["error"]:
                        st.error(f"Error: {s['error']}")
                    audit_path = s["audit_path"]
                    if audit_path and os.path.exists(audit_path):
                        with open(audit_path) as f:
                            record = json.load(f)
                        st.markdown("**Raw model response:**")
                        st.code(record.get("raw_response") or "(empty)", language="json")
                        st.markdown("**Parsed:**")
                        st.json(record.get("parsed") or {})
                        st.caption(f"Audit file: {audit_path}")
                    else:
                        st.caption("Audit record not found.")


# --------------------------------------------------------------------------
# Live loop driver: schedule the next cycle after rendering this one.
# --------------------------------------------------------------------------
if _do_live_rerun and st.session_state["live_on"]:
    time.sleep(st.session_state["live_interval"])
    st.rerun()
