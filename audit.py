"""Step 4: Audit trail.

Every capture saves BOTH the image (already written to /captures/ by
capture.py) AND the full raw model response — not just the parsed numbers —
so a human can later open the image next to what the AI actually said and
judge whether the count and expiry reads were correct. This is how real
accuracy gets measured.

One JSON file is written to /audit/ per snapshot, named to match the image
basename so image <-> response correlation is trivial.
"""

import json
import os

AUDIT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit")


def save_audit_record(timestamp_iso, image_path, raw_text, parsed, error, model):
    """Write a full audit record and return its path.

    Stores the raw model text verbatim (result of Step 2), the parsed JSON if
    parsing succeeded, any error, the model used, and the image path.
    """
    os.makedirs(AUDIT_DIR, exist_ok=True)

    image_base = os.path.basename(image_path) if image_path else "unknown"
    stem = os.path.splitext(image_base)[0]
    audit_path = os.path.join(AUDIT_DIR, f"{stem}.json")

    record = {
        "timestamp": timestamp_iso,
        "model": model,
        "image_path": image_path,
        "raw_response": raw_text,
        "parsed": parsed,
        "error": error,
    }
    with open(audit_path, "w") as f:
        json.dump(record, f, indent=2)

    return audit_path
