# Ward Storeroom Inventory — Camera POC

Internal proof-of-concept testing whether a camera can help track decentralized
ward storeroom stock. Two apps live here:

- **`inventory_app.py`** — barcode inventory web-app (phone-friendly, no login).
  Persistent store: view inventory, view expiry, and count stock either manually
  or by scanning a barcode with the camera. **This is the app deployed to the
  cloud.**
- **`app.py`** — the earlier AI-vision POC (snapshot + near-live counting of
  items from a photo, with an audit trail). Local only; needs an Anthropic API
  credential.

## Run locally

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-local.txt      # full local set (incl. vision POC)
streamlit run inventory_app.py             # the barcode inventory app
# or: streamlit run app.py                 # the AI-vision POC (needs ANTHROPIC_API_KEY)
```

## Deploy the inventory app (Streamlit Community Cloud)

1. Push this repo to GitHub.
2. Go to https://share.streamlit.io → **Create app** → pick this repo.
3. Set **Main file path** to `inventory_app.py`.
4. Deploy. Streamlit installs `requirements.txt` (headless OpenCV — no system
   packages needed) and gives you an **HTTPS** URL that works on a phone,
   including the camera.

### Honest caveats for the hosted version

- **Data leaves your machine.** Photos and inventory data are processed on
  Streamlit's servers. Fine for desk-item testing; needs a proper review before
  any real ward/medical data.
- **Storage is ephemeral.** `store.db` (SQLite) resets when the app sleeps or
  redeploys. It's a demo store, not a lasting one — a real deployment needs an
  external database.
- **GS1 DataMatrix (2D pharma codes)** need the optional `pylibdmtx` library to
  decode. 1D barcodes (EAN/UPC) and QR codes work out of the box.

## Barcode support

| Type | Identity + count | Expiry from scan |
|---|---|---|
| 1D retail (EAN/UPC) | ✅ | typed in manually |
| QR (incl. GS1-QR) | ✅ | ✅ if GS1-encoded |
| GS1 DataMatrix (pharma) | needs `pylibdmtx` | ✅ once that's installed |
