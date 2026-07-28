# Ward Storeroom Inventory — Camera POC

Internal proof-of-concept testing whether a camera can help track decentralized
ward storeroom stock. Two apps live here:

- **`inventory_app.py`** — the barcode inventory web-app (phone/tablet friendly,
  no login). **This is the app deployed to the cloud.**
- **`app.py`** — the earlier AI-vision POC (counting items from a photo, with an
  audit trail). Local only; needs an Anthropic API credential.

## The inventory app

Two audiences, deliberately separated into different tabs.

**Admin — `📦 Inventory` and `⏰ Expiry`**

- Register items and set a minimum stock level (the app flags anything below it).
- Receive stock, with optional expiry and lot per batch.
- **Generate a printable barcode label** for items that don't carry one. A
  unique `WARD-XXXXXX` code is minted, rendered as Code128 (or QR, which reads
  better on small or curved items like vials), and downloadable as a PNG to
  print and stick on the item or its shelf bin.
- Correct mistakes: edit names and minimums, adjust or delete batches, delete items.

**User — `➖ Consume` and `➕ Add Back`**

- Press **Start scanning** and point the camera at a basket. Codes accumulate
  live, with a beep and a green flash per new code — no button press per item,
  so you can prop a tablet over the basket and pass items through frame.
- **Nothing is committed until you confirm.** Each distinct barcode is listed
  once with an editable quantity.
- `Consume` depletes stock **first-expiry-first-out**. `Add Back` returns items
  to the soonest-expiring batch so they keep their original expiry.

**`🧾 Activity`** shows the movement log — every consume, add-back and receipt.

### Why each barcode is only counted once

Three identical syringes carry three *identical* barcodes. A camera cannot tell
"the same item still in frame" from "a second identical item", so the scanner
never infers quantity from repeat detections. It reports distinct codes; you set
the quantity in the review step. This is also what stops a stray frame from
silently double-consuming.

### Scanning is done in the browser

`components/live_scanner/index.html` streams the camera and decodes with
[ZXing](https://github.com/zxing-js/library) client-side, as a small custom
Streamlit component (the component protocol is implemented directly over
`postMessage`, so there is no npm build step). This means:

- Camera frames never reach the server.
- No WebRTC/TURN traversal, which is what makes `streamlit-webrtc` unreliable on
  Community Cloud.
- No OpenCV on the server at all — hence its absence from `requirements.txt`.
- DataMatrix works out of the box; the old `pylibdmtx`/`libdmtx` requirement is gone.

Two things it needs: **HTTPS** (browsers only grant camera access over HTTPS or
on `localhost`, so a `http://192.168.x.x` LAN address will not work), and access
to `unpkg.com` for the ZXing library. If the network blocks CDNs, vendor ZXing
into the repo and change the `<script src>`. Both flows also have a
**type-the-code-by-hand** fallback, which is why the label prints the code in text.

## Run locally

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-local.txt      # full local set (incl. vision POC)
streamlit run inventory_app.py             # the inventory app
# or: streamlit run app.py                 # the AI-vision POC (needs ANTHROPIC_API_KEY)
```

Use the **localhost** URL for camera testing. For a phone, deploy and use the
HTTPS URL — the LAN address won't get camera permission.

## Deploy (Streamlit Community Cloud)

1. Push to GitHub.
2. https://share.streamlit.io → **Create app** → pick this repo. For a private
   repo you must grant Streamlit the extra GitHub permission, or it won't appear.
3. **Main file path**: `inventory_app.py`
4. Deploy. `requirements.txt` is installed; no system packages needed.

### Honest caveats for the hosted version

- **The app is public even if the repo is private.** A Community Cloud URL is
  reachable by anyone who has it, and this app has no login — so anyone with the
  link can read *and change* stock. Restrict viewers to invited emails before
  this holds anything real.
- **Storage is ephemeral.** `store.db` (SQLite) resets when the app sleeps or
  redeploys. It's a demo store, not a lasting one — a real deployment needs an
  external database.
- **Inventory data is processed on Streamlit's servers.** Camera frames are not
  (they stay in the browser), but item names, counts and expiry dates are. Fine
  for desk-item testing; needs a proper review before any real ward or patient data.

## Barcode support

| Type | Identity + count | Expiry from scan |
|---|---|---|
| 1D retail (EAN/UPC) | ✅ | typed in manually |
| Code128 (incl. generated `WARD-` labels) | ✅ | typed in manually |
| QR (incl. GS1-QR) | ✅ | ✅ if GS1-encoded |
| GS1 DataMatrix (pharma) | ✅ | ✅ |
