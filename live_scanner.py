"""Python side of the live barcode scanner component.

Wraps components/live_scanner/index.html, which streams the camera and decodes
barcodes in the browser. See that file for why decoding is client-side.

Returns the list of distinct codes accumulated so far in this scanning session:

    [{"code": "...", "format": "EAN-13", "at": <js ms timestamp>}, ...]

Each distinct code appears once. Quantity is deliberately NOT inferred from
repeat detections — see the note in index.html — so callers must confirm
quantities before committing anything to stock.
"""

import os

import streamlit.components.v1 as components

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "components", "live_scanner")

_component = components.declare_component("live_scanner", path=_DIR)


def live_scanner(key=None):
    """Render the scanner. Returns a list of {code, format, at} dicts."""
    value = _component(key=key, default={"codes": [], "n": 0})
    if not isinstance(value, dict):
        return []
    codes = value.get("codes") or []
    return [c for c in codes if isinstance(c, dict) and c.get("code")]
