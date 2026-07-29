"""Standard units of measure for inventory.

Codes follow UN/ECE Recommendation 20, the same list GS1 uses, so these travel
cleanly into any downstream system rather than being invented here.

Only the units a ward store actually issues stock in are included — the full
recommendation runs to hundreds of codes and picking from those in a dropdown
would be worse than useless.
"""

# code -> (label, description)
UOMS = {
    "EA":  ("Each", "single item"),
    "PR":  ("Pair", "two items issued together"),
    "DZ":  ("Dozen", "12 items"),
    "PK":  ("Pack", "manufacturer pack"),
    "BX":  ("Box", "box"),
    "CT":  ("Carton", "carton"),
    "CS":  ("Case", "case / shipper"),
    "BG":  ("Bag", "bag"),
    "RL":  ("Roll", "roll"),
    "ST":  ("Set", "set"),
    "KT":  ("Kit", "kit"),
    "TU":  ("Tube", "tube"),
    "VI":  ("Vial", "vial"),
    "AM":  ("Ampoule", "ampoule"),
    "BO":  ("Bottle", "bottle"),
    "SA":  ("Sachet", "sachet"),
    "MLT": ("Millilitre", "volume, mL"),
    "LTR": ("Litre", "volume, L"),
    "GRM": ("Gram", "mass, g"),
    "KGM": ("Kilogram", "mass, kg"),
    "MTR": ("Metre", "length, m"),
    "CMT": ("Centimetre", "length, cm"),
}

DEFAULT_UOM = "EA"

# Ordered for the dropdown: countable units first, then measures.
ORDER = [
    "EA", "PR", "DZ", "PK", "BX", "CT", "CS", "BG", "RL", "ST", "KT",
    "TU", "VI", "AM", "BO", "SA",
    "MLT", "LTR", "GRM", "KGM", "MTR", "CMT",
]


def codes():
    return list(ORDER)


def label(code):
    entry = UOMS.get((code or "").upper())
    return entry[0] if entry else (code or "")


def display(code):
    """'EA — Each (single item)', for dropdowns."""
    c = (code or "").upper()
    entry = UOMS.get(c)
    if not entry:
        return c
    return f"{c} — {entry[0]} ({entry[1]})"


def is_valid(code):
    return (code or "").upper() in UOMS


def normalize(code, default=DEFAULT_UOM):
    c = (code or "").strip().upper()
    return c if c in UOMS else default
