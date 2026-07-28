"""Catalog config loader + name matching.

The catalog lives in config.json (edit it to change items and min-stock levels):

    {"catalog": [{"name": "coffee mug", "min_stock": 2}, ...]}
"""

import json
import os
import re

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def load_catalog():
    with open(CONFIG_PATH) as f:
        data = json.load(f)
    return data.get("catalog", [])


def catalog_names():
    return [item["name"] for item in load_catalog()]


def min_stock_map():
    return {item["name"]: int(item.get("min_stock", 0)) for item in load_catalog()}


def _normalize(name):
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def match_to_catalog(detected_name):
    """Map a model-detected item name to a catalog name, or None.

    Rough matching for a POC: exact normalized match first, then a lenient
    substring match either direction (so "white coffee mug" or "mug" both
    map to catalog "coffee mug"). Returns the canonical catalog name or None
    if nothing matches.
    """
    d = _normalize(detected_name)
    if not d:
        return None
    names = catalog_names()
    norm_to_name = {_normalize(n): n for n in names}

    if d in norm_to_name:
        return norm_to_name[d]

    for norm, original in norm_to_name.items():
        if norm and (norm in d or d in norm):
            return original
    return None
