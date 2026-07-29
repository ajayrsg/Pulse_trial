# Ward Storeroom Inventory — batch-scanning stock control

Internal proof-of-concept. A camera counts a whole basket of items at once so
stocking up and withdrawing take seconds instead of a scan-per-item.

- **`inventory_app.py`** — the app. **This is what gets deployed.**
- **`app.py`** — an earlier AI-vision experiment (counting items from a photo).
  Local only, needs an Anthropic API credential, not part of the flow above.

## The idea that makes it work

Three identical syringes carry three **identical** barcodes, so counting unique
codes would read them as one. Quantity therefore comes from **position**: every
decode's location is mapped into frame coordinates, and detections of the same
code at different places are different items.

Two problems had to be solved for that to hold:

- A decode returns at most one barcode. So each region is decoded repeatedly,
  whiting out each symbol as it is found, until nothing is left — that is what
  lets several items (identical or not) come out of one frame.
- Masking has to be exact. Too little and the unmasked remainder of a tall
  symbol is re-found as a second item; too much and it clips the neighbours in a
  packed basket. So a symbol's real vertical extent is measured rather than
  guessed: rows of bars contain many dark/light transitions, blank rows do not.

Counting is done per sweep, taking the highest reading per code. Accumulating a
union of positions across sweeps was tried and rejected — it raised counts in
dense baskets but let jitter turn one barcode into two, and a silent overcount on
withdrawal takes stock off the record that is still on the shelf.

### No confirm step

Counts rise until nothing new has appeared for ~2.5s, then the batch **commits by
itself**. After committing, the basket is usually still under the camera, so a
new batch cannot start until the frame goes empty. Without that gate the same
items would post twice. A **Next batch** button is there for when the items
cannot be cleared from view.

Because nothing is confirmed by a person, a wrong count needs somewhere to be
fixed. Two places do it, both logging an adjustment: the **Stock take** screen,
and **Admin → Storerooms → Inventory Assigned → Correct a count**.

## Day-to-day (phone / tablet)

The app opens on a **landing page**, not a row of tabs: the storeroom name, then
**Quick actions** as cards, then an **Inventory overview** with a stock-status
ring. Picking an action goes to that one screen with a back link, so a nurse sees
a short list of choices and then one job at a time.

Cards are real anchors driven by a `nav` query parameter rather than session
state — that allows the card layout (icon above label, which a Streamlit button
cannot do) and makes the browser's own back button work. Cards a role cannot use
are not rendered, and hitting the URL directly is refused as well.

The theme is pinned light in `.streamlit/config.toml`: the cards and ring are
designed on light surfaces, so a viewer's dark mode would otherwise wreck them.

| Screen | What it does |
|---|---|
| **Stock up** | Batch-scan items in. Set the expiry before scanning; a GS1 pharma code that carries its own expiry overrides it per item. Stocking an expiry already on the shelf **adds to that count** rather than creating a second batch. |
| **Withdraw** | Batch-scan items out, oldest expiry first. Shows a large confirmation listing what went, with the expiry taken. |
| **Transfer** | Send stock to another storeroom in the agency, chosen by typing to filter. Expiry dates travel with it. The destination must already carry the item. |
| **Dispose** | Scan or select an item, pick the batch, give a reason. **This one does confirm** — it is the only action with no counterpart record to reconcile against. |
| **Stock take** | Count what is on the shelf and correct the record, logged as an adjustment. Team Admin and above, since it overrides recorded counts. |
| **Activity** | Every movement: inflow/outflow, item, quantity, the expiry that moved, disposal reason, the other storeroom, and who. |
| **Inventory / Low Stock / Expiry** | Current counts, anything below its minimum, and dated stock soonest-first. |

## Admin (desktop)

- **Storerooms** — create them, then per storeroom: **Users Assigned**
  (assign/unassign/delete), **Inventory Assigned** (assign master items with a
  min and opening quantity; an item with stock on hand **cannot** be unassigned;
  correct counts here), **Webhook**, and rename/delete.
- **Master Inventory** — the agency's item list: name, barcode, and a unit of
  measure from the standard set (UN/ECE Rec 20, the list GS1 uses). CSV upload
  for an existing list. Shows which storerooms carry each item.
- **Users** — three roles:

| Role | Can do |
|---|---|
| **App Admin** | Everything, plus manage inventory, storerooms, users, webhooks |
| **Team Admin** | Everything a User can, plus **Dispose**, **Transfer** and **Stock take** |
| **User** | Stock up, Withdraw, Activity, Inventory, Low Stock, Expiry |

### Webhooks

Each storeroom can hold a webhook URL, and the payload covers both wanted
triggers: items expiring inside a horizon (a month by default) and anything below
its minimum. There is a **Send now** button to test it.

**This app cannot schedule anything.** Streamlit only runs while a browser
session is open, so the monthly and low-stock reports must come from an external
scheduler (Plumber, cron, a CI job) calling in. The payload preview shows what to
expect.

## Measured behaviour

Verified by feeding rendered barcodes to headless Chrome as a fake camera and
running the real component:

| Scene | Result |
|---|---|
| Single Code128 / EAN-13 / QR, 35–80% of frame | exact, first decode ~15ms |
| 3 distinct codes in frame | 3/3 |
| Mixed EAN-13 + Code128 + GS1-QR + Code128 | 4/4 |
| 3 **identical** codes + 1 distinct | 3 and 1 — exact |
| 12 items (4 products × 3 copies), 1080p | 12/12, ~9s of scanning |

**The limit is optical, not algorithmic.** A barcode needs roughly 2px per narrow
bar to decode; below that nothing helps. A 4×5 grid of 11-character Code128
symbols in a 720p frame works out at ~1.5px per bar and is simply unreadable —
that scene counted 10–14 of 20. At 4.4px per bar it counts exactly. Practically:
**spread items out, fill the frame, and scan in batches of roughly a dozen**
rather than tipping 100 items under the camera at once. On those numbers 100
items lands inside two minutes across several batches.

## Run locally

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-local.txt
streamlit run inventory_app.py
```

Use the **localhost** URL for camera testing. For a phone, deploy and use the
HTTPS URL — browsers only grant camera access over HTTPS or on `localhost`, so a
`http://192.168.x.x` LAN address will not work.

First run offers to create an App Admin, or to **load a small demo setup** (two
storerooms, three users covering every role, five items with stock).

## Deploy (Streamlit Community Cloud)

1. Push to GitHub.
2. share.streamlit.io → **Create app** → pick this repo. A private repo needs the
   extra GitHub permission granted, or it will not appear.
3. **Main file path**: `inventory_app.py`
4. Deploy. `requirements.txt` is installed; no system packages needed.

### Honest caveats

- **There is no authentication.** The sidebar picks who you are acting as, which
  demonstrates the three roles but is not a login. Anyone with the URL can act as
  anyone, including App Admin.
- **A Community Cloud app is public even from a private repo.** Restrict viewers
  to invited emails before this holds anything real.
- **Storage is ephemeral.** `ward.db` (SQLite) resets when the app sleeps or
  redeploys. A real deployment needs an external database.
- **Camera frames stay on the device** — decoding is client-side. Item names,
  counts and expiry dates are processed on Streamlit's servers.
- The scanner fetches ZXing from `unpkg.com`. If the network blocks CDNs it says
  so rather than failing silently; vendoring the library is a small change.

## Barcode support

| Type | Identity + count | Expiry from scan |
|---|---|---|
| 1D retail (EAN/UPC) | ✅ | typed in per batch |
| Code128 / Code39 / ITF / Codabar | ✅ | typed in per batch |
| QR (incl. GS1-QR) | ✅ | ✅ if GS1-encoded |
| GS1 DataMatrix (pharma) | ✅ | ✅ |
