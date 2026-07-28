"""Generate printable barcode labels for items that have no manufacturer code.

Two symbologies, because they suit different situations:

  - Code128 (default): a real 1D barcode. Encodes our WARD-XXXXXX internal
    codes directly, and reads on both the phone camera flow and any handheld
    laser scanner a ward might already own.
  - QR: more forgiving of angle, curvature and poor light, so it wins on small
    or curved items (vials, ampoules) where a 1D barcode can't lie flat.

Both render to PNG for on-screen preview and printing. Output is a label —
item name above, barcode below, human-readable code underneath — so a nurse
can still read the code by eye when a scan fails.

Dependencies degrade gracefully: if python-barcode / qrcode aren't installed
the app keeps working and simply reports that label printing is unavailable.
"""

import io

LABEL_W = 600  # px; ~50mm at 300dpi, a common label-printer width
_MARGIN = 16


def code128_available():
    try:
        import barcode  # noqa: F401
        from barcode.writer import ImageWriter  # noqa: F401

        return True
    except Exception:
        return False


def qr_available():
    try:
        import qrcode  # noqa: F401

        return True
    except Exception:
        return False


def any_available():
    return code128_available() or qr_available()


def _load_font(size):
    """A legible font, falling back through common system paths."""
    from PIL import ImageFont

    candidates = [
        "/System/Library/Fonts/Helvetica.ttc",  # macOS
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Debian/Ubuntu
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _text_size(draw, text, font):
    """Width/height of `text`, across Pillow versions."""
    try:
        box = draw.textbbox((0, 0), text, font=font)
        return box[2] - box[0], box[3] - box[1]
    except AttributeError:  # very old Pillow
        return draw.textsize(text, font=font)


def _code128_png(code):
    import barcode
    from barcode.writer import ImageWriter

    writer = ImageWriter()
    obj = barcode.get("code128", code, writer=writer)
    buf = io.BytesIO()
    # dpi=254 makes the mm->px factor exactly 10, so module_width 0.5mm renders
    # as exactly 5px. Whole-pixel modules matter: at a fractional width the bars
    # land off-pixel and every bar edge picks up a rounding error, which is what
    # makes a printed barcode read intermittently.
    #
    # write_text=False: we draw the human-readable code ourselves so its size
    # and placement match the rest of the label.
    obj.write(
        buf,
        options={
            "dpi": 254,
            "module_width": 0.5,
            "module_height": 18.0,
            "quiet_zone": 2.5,
            "write_text": False,
        },
    )
    buf.seek(0)
    return buf


def _qr_png(code):
    import qrcode

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(code)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def make_label(code, name="", symbology="code128"):
    """Render a printable PNG label. Returns bytes, or raises RuntimeError.

    symbology: "code128" | "qr"
    """
    from PIL import Image, ImageDraw

    if symbology == "qr":
        if not qr_available():
            raise RuntimeError("QR generation needs the 'qrcode' package.")
        sym_buf = _qr_png(code)
    else:
        if not code128_available():
            raise RuntimeError("Code128 generation needs the 'python-barcode' package.")
        sym_buf = _code128_png(code)

    sym = Image.open(sym_buf).convert("RGB")

    # Never resample the symbology to fit a fixed width. Smooth interpolation
    # (LANCZOS/bilinear) antialiases bar edges, which is exactly what breaks a
    # scan — a decoder needs hard black/white transitions. So the canvas grows
    # to fit the barcode at its native size instead. Only an oversized code is
    # scaled, and then with NEAREST so edges stay crisp.
    max_w = max(LABEL_W, sym.width + 2 * _MARGIN)
    if sym.width > max_w - 2 * _MARGIN:
        target_w = max_w - 2 * _MARGIN
        scale = target_w / sym.width
        sym = sym.resize((target_w, max(1, int(sym.height * scale))), Image.NEAREST)

    canvas_w = max(LABEL_W, sym.width + 2 * _MARGIN)

    name_font = _load_font(34)
    code_font = _load_font(26)

    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    name_h = _text_size(probe, name, name_font)[1] if name else 0
    code_h = _text_size(probe, code, code_font)[1]

    gap = 10
    total_h = _MARGIN + (name_h + gap if name else 0) + sym.height + gap + code_h + _MARGIN
    canvas = Image.new("RGB", (canvas_w, total_h), "white")
    draw = ImageDraw.Draw(canvas)

    y = _MARGIN
    if name:
        w = _text_size(draw, name, name_font)[0]
        draw.text(((canvas_w - w) // 2, y), name, fill="black", font=name_font)
        y += name_h + gap

    canvas.paste(sym, ((canvas_w - sym.width) // 2, y))
    y += sym.height + gap

    w = _text_size(draw, code, code_font)[0]
    draw.text(((canvas_w - w) // 2, y), code, fill="black", font=code_font)

    out = io.BytesIO()
    canvas.save(out, format="PNG")
    return out.getvalue()
