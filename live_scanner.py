"""Python side of the batch barcode scanner.

Wraps components/live_scanner/index.html, which streams the camera, decodes in
the browser, counts each code by how many distinct positions it occupies in the
frame, and commits a batch by itself once nothing new has appeared for a moment.

Returned value:

    {
      "state": "idle" | "scanning" | "waiting_clear",
      "live":  [{"code": ..., "qty": n, "format": ...}],   # counts so far
      "batch": {"id": n, "items": [...], "units": n} | None,
      "nonce": n,
    }

`batch` is non-null on the render right after a commit. Callers must apply a
given batch id at most once — use `take_batch()`, which handles that.
"""

import os

import streamlit as st
import streamlit.components.v1 as components

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "components", "live_scanner")

_component = components.declare_component("live_scanner", path=_DIR)

_EMPTY = {"state": "idle", "live": [], "batch": None, "nonce": 0}


def live_scanner(key, mode="scan", settle_ms=2500):
    """Render the scanner. `mode` is a label for the component's own display."""
    value = _component(key=key, mode=mode, settle_ms=int(settle_ms), default=_EMPTY)
    if not isinstance(value, dict):
        return dict(_EMPTY)
    return value


def take_batch(value, seen_key):
    """Return a freshly committed batch exactly once, else None.

    The component keeps reporting the same batch until it is superseded, so the
    last applied id is remembered in session state. Without this a rerun would
    post the same stock movement again.
    """
    batch = (value or {}).get("batch")
    if not batch:
        return None
    bid = batch.get("id")
    if bid is None:
        return None
    last = st.session_state.get(seen_key)
    if last is not None and bid <= last:
        return None
    st.session_state[seen_key] = bid
    return batch
