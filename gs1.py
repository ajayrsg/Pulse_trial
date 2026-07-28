"""Minimal GS1 element-string parser.

Retail 1D barcodes (EAN/UPC) carry ONLY a product number — no expiry. GS1
barcodes (GS1-128, GS1 DataMatrix, GS1 QR — common on pharmaceuticals) encode
several fields via Application Identifiers (AIs), e.g.:
    01 = GTIN (14 digits)      17 = expiry (YYMMDD)      10 = batch/lot

This parser turns a decoded GS1 string into {gtin, expiry (date), lot, raw_ais}.
It's deliberately small — it handles the AIs an inventory POC needs, not the
full GS1 spec.
"""

from datetime import date, timedelta

# ASCII Group Separator — the FNC1 separator in scanned GS1 data.
GS = "\x1d"

# AIs with a fixed value length (so no separator is needed after them).
_FIXED_LEN = {
    "00": 18, "01": 14, "11": 6, "12": 6, "13": 6,
    "15": 6, "16": 6, "17": 6, "20": 2,
}

# Common symbology identifier prefixes a scanner may prepend.
_SYMBOLOGY_PREFIXES = ("]C1", "]e0", "]d2", "]Q3", "]Q1")


def looks_like_gs1(data):
    """Heuristic: does this decoded string look like a GS1 element string?"""
    if not data:
        return False
    s = _strip_prefix(data)
    return s[:2] in _FIXED_LEN or GS in data or data.startswith("]")


def _strip_prefix(data):
    s = data
    for p in _SYMBOLOGY_PREFIXES:
        if s.startswith(p):
            s = s[len(p):]
            break
    return s.lstrip(GS)


def parse_gs1_date(s):
    """Parse a GS1 YYMMDD date. Day '00' means end of month. Returns date|None."""
    if len(s) != 6 or not s.isdigit():
        return None
    yy, mm, dd = int(s[:2]), int(s[2:4]), int(s[4:6])
    year = 2000 + yy  # POC window; GS1's sliding window is not needed for near-future expiries
    if not 1 <= mm <= 12:
        return None
    if dd == 0:
        first_next = date(year + 1, 1, 1) if mm == 12 else date(year, mm + 1, 1)
        return first_next - timedelta(days=1)
    try:
        return date(year, mm, dd)
    except ValueError:
        return None


def parse_gs1(data):
    """Parse a GS1 element string into a dict of AI -> value.

    Returns {} if the string doesn't parse as GS1.
    """
    s = _strip_prefix(data)
    ais = {}
    i = 0
    while i < len(s):
        ai = s[i:i + 2]
        i += 2
        if ai in _FIXED_LEN:
            length = _FIXED_LEN[ai]
            val = s[i:i + length]
            i += length
        else:
            j = s.find(GS, i)
            if j == -1:
                j = len(s)
            val = s[i:j]
            i = j
        if i < len(s) and s[i] == GS:
            i += 1
        if not ai.isdigit():
            break  # not a well-formed AI stream; stop rather than guess
        ais[ai] = val
    return ais


def extract(data):
    """High-level: return {gtin, expiry (date|None), lot, raw_ais} from a scan.

    For a non-GS1 payload (plain EAN/UPC), returns gtin=the raw code, no expiry.
    """
    if looks_like_gs1(data):
        ais = parse_gs1(data)
        if ais:
            return {
                "gtin": ais.get("01"),
                "expiry": parse_gs1_date(ais["17"]) if "17" in ais else None,
                "lot": ais.get("10"),
                "raw_ais": ais,
            }
    # Plain product barcode: the whole code is the identifier, no expiry in it.
    return {"gtin": data, "expiry": None, "lot": None, "raw_ais": {}}
