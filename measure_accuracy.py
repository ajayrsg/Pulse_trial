"""Accuracy measurement pass.

The honest counterpart to the live view: run the model against images you
have hand-counted, and report how close it actually gets. This is the number
to put in front of the product team — a measured result, not a promise.

Usage:
    # 1. First run creates a ground_truth.json template from /captures/:
    python measure_accuracy.py

    # 2. Open ground_truth.json and fill in the TRUE counts you counted by
    #    hand for each image (only images with a non-empty "counts" object
    #    are scored). Then run again to get the report:
    python measure_accuracy.py

Requires a working credential (same as vision.py) since it calls the model.
"""

import csv
import json
import os
import sys

from poc_config import catalog_names, match_to_catalog
from vision import analyze_image

HERE = os.path.dirname(os.path.abspath(__file__))
CAPTURES_DIR = os.path.join(HERE, "captures")
GT_PATH = os.path.join(HERE, "ground_truth.json")
REPORT_PATH = os.path.join(HERE, "accuracy_report.csv")


def _predicted_catalog_counts(parsed):
    """Map a parsed model result to {catalog_item: count}."""
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


def _write_template():
    images = []
    if os.path.isdir(CAPTURES_DIR):
        images = sorted(
            f for f in os.listdir(CAPTURES_DIR)
            if f.lower().endswith((".jpg", ".jpeg", ".png"))
        )
    template = {
        "_instructions": (
            "For each image, fill 'counts' with the TRUE number of each catalog "
            "item you counted by hand. Leave an image's counts empty to skip it. "
            "Catalog items: " + ", ".join(catalog_names())
        ),
        "images": [
            {"file": os.path.join("captures", f), "counts": {}} for f in images
        ],
    }
    with open(GT_PATH, "w") as fh:
        json.dump(template, fh, indent=2)
    return len(images)


def main():
    if not os.path.exists(GT_PATH):
        n = _write_template()
        print(f"Created {GT_PATH} listing {n} image(s) from /captures/.")
        print("Fill in the true 'counts' per image, then run this again.")
        return 0

    with open(GT_PATH) as fh:
        gt = json.load(fh)

    scored = [e for e in gt.get("images", []) if e.get("counts")]
    if not scored:
        print(
            f"No images have hand-counted 'counts' filled in yet in {GT_PATH}.\n"
            "Fill some in and run again."
        )
        return 0

    catalog = catalog_names()
    rows = []
    exact_cells = 0
    total_cells = 0
    abs_error_sum = 0
    images_fully_correct = 0

    for entry in scored:
        rel = entry["file"]
        path = rel if os.path.isabs(rel) else os.path.join(HERE, rel)
        truth = {k: int(v) for k, v in entry["counts"].items()}
        print(f"Analyzing {rel} …")
        try:
            result = analyze_image(path)
            parsed = result["parsed"]
            pred = _predicted_catalog_counts(parsed)
            err = result["error"]
        except Exception as e:  # noqa: BLE001
            print(f"  ! failed: {type(e).__name__}: {e}")
            continue

        image_ok = True
        for item in catalog:
            if item not in truth:
                continue  # only score items the human actually counted
            t = truth[item]
            p = pred.get(item, 0)
            match = (t == p)
            exact_cells += 1 if match else 0
            total_cells += 1
            abs_error_sum += abs(t - p)
            if not match:
                image_ok = False
            rows.append(
                {
                    "image": rel,
                    "item": item,
                    "true_count": t,
                    "predicted_count": p,
                    "abs_error": abs(t - p),
                    "exact_match": match,
                }
            )
        if image_ok:
            images_fully_correct += 1
        if parsed and parsed.get("notes"):
            print(f"  model notes: {parsed['notes']}")

    if total_cells == 0:
        print("Nothing scored — check that 'counts' use catalog item names.")
        return 0

    with open(REPORT_PATH, "w", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "image", "item", "true_count", "predicted_count",
                "abs_error", "exact_match",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    exact_rate = 100.0 * exact_cells / total_cells
    mae = abs_error_sum / total_cells
    print()
    print("=" * 56)
    print("ACCURACY REPORT (counts only — expiry read separately by eye)")
    print("=" * 56)
    print(f"Images scored:            {len(scored)}")
    print(f"Item-cells scored:        {total_cells}")
    print(f"Exact per-item matches:   {exact_cells}/{total_cells} "
          f"({exact_rate:.1f}%)")
    print(f"Mean absolute count error:{mae:.2f} units per item")
    print(f"Images fully correct:     {images_fully_correct}/{len(scored)}")
    print(f"\nPer-item detail written to: {REPORT_PATH}")
    print(
        "\nNote: this measures COUNTS. Expiry-date accuracy should be judged by "
        "reading each audit record by eye — OCR of small label text is the "
        "weakest part and isn't captured by these numbers."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
