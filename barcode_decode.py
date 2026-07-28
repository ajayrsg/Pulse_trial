"""Decode barcodes from an image — holistic across symbologies.

Decoders, in order of what's available:
  - 1D (EAN-13 / UPC-A / EAN-8 ...): cv2.barcode.BarcodeDetector   (installed)
  - QR codes:                        cv2.QRCodeDetector             (installed)
  - GS1 DataMatrix (2D pharma):      pylibdmtx, IF it is installed  (optional)

DataMatrix is where medical expiry+lot usually live. pylibdmtx needs the
system 'libdmtx' library; if it isn't installed the app still runs and simply
can't read DataMatrix — everything else works, and this lights up the moment
the library is present.
"""

import cv2
import numpy as np


def _decode_1d(img):
    out = []
    try:
        detector = cv2.barcode.BarcodeDetector()
        ok, infos, types, _pts = detector.detectAndDecodeMulti(img)
        if ok and infos is not None:
            types = list(types) if types is not None else []
            for idx, info in enumerate(infos):
                if info:
                    sym = str(types[idx]) if idx < len(types) else "1D"
                    out.append({"data": info, "symbology": sym})
    except Exception:
        pass
    return out


def _decode_qr(img):
    out = []
    try:
        detector = cv2.QRCodeDetector()
        ok, infos, _pts, _ = detector.detectAndDecodeMulti(img)
        if ok and infos is not None:
            for info in infos:
                if info:
                    out.append({"data": info, "symbology": "QR"})
    except Exception:
        pass
    return out


def _decode_datamatrix(img):
    """Only works if pylibdmtx (and libdmtx) is installed. Silent no-op otherwise."""
    try:
        from pylibdmtx.pylibdmtx import decode as dm_decode
    except Exception:
        return []
    out = []
    try:
        for r in dm_decode(img):
            out.append(
                {"data": r.data.decode("utf-8", "replace"), "symbology": "DataMatrix"}
            )
    except Exception:
        pass
    return out


def datamatrix_available():
    try:
        import pylibdmtx.pylibdmtx  # noqa: F401
        return True
    except Exception:
        return False


def decode_image_bytes(image_bytes):
    """Return a de-duplicated list of {data, symbology} decoded from the image."""
    arr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return []

    results = _decode_1d(img) + _decode_qr(img) + _decode_datamatrix(img)

    seen, deduped = set(), []
    for r in results:
        if r["data"] and r["data"] not in seen:
            seen.add(r["data"])
            deduped.append(r)
    return deduped
