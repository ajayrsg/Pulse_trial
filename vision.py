"""Step 2: Vision-based count + expiry read.

Sends a captured frame to Claude for item counting and expiry-date extraction.
Run directly to analyze the most recent capture in /captures/, or import
`analyze_image()` from other scripts (e.g. the Streamlit app).
"""

import base64
import json
import os
import sys

from imgutil import detect_media_type
from llm_client import make_client

CAPTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captures")

MODEL = "claude-opus-5"

PROMPT = """You are looking at a photo of a single-layer shelf or locker in a \
hospital ward storeroom. Items are facing the camera and spaced apart, not stacked.

Count the visible items and identify any expiry dates printed or written on them.

Respond with STRICT JSON only, no markdown fences, no commentary, matching exactly \
this shape:

{
  "items": [
    {"name": "...", "count": N, "confidence": "high|medium|low", "expiry_dates_found": ["..."]},
    ...
  ],
  "notes": "anything ambiguous, occluded, or uncertain"
}

If you see items you can't confidently identify, still list them with your best \
guess at a name and set confidence to "low". If no expiry dates are visible for an \
item, use an empty list for expiry_dates_found."""


def _extract_text(response):
    for block in response.content:
        if block.type == "text":
            return block.text
    return ""


def _strip_code_fence(text):
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def analyze_image(image_path, client=None):
    """Send an image to Claude and parse the strict-JSON response.

    Returns a dict: {"parsed": dict|None, "raw_text": str, "error": str|None}
    Retries once on malformed JSON before giving up and returning the raw text.
    """
    client = client or make_client()

    with open(image_path, "rb") as f:
        raw = f.read()
    # Detect the true media type from the bytes — the extension can lie
    # (the browser camera widget hands us JPEG even when we save as .png).
    media_type = detect_media_type(raw)
    image_data = base64.standard_b64encode(raw).decode("utf-8")

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": image_data,
                    },
                },
                {"type": "text", "text": PROMPT},
            ],
        }
    ]

    raw_text = ""
    for attempt in range(2):
        response = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            output_config={"effort": "high"},
            messages=messages,
        )
        raw_text = _extract_text(response)
        candidate = _strip_code_fence(raw_text)
        try:
            parsed = json.loads(candidate)
            return {"parsed": parsed, "raw_text": raw_text, "error": None}
        except json.JSONDecodeError as e:
            if attempt == 0:
                messages.append({"role": "assistant", "content": raw_text})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "That was not valid JSON. Respond again with ONLY the "
                            "strict JSON object, no markdown fences, no other text."
                        ),
                    }
                )
                continue
            return {"parsed": None, "raw_text": raw_text, "error": str(e)}

    return {"parsed": None, "raw_text": raw_text, "error": "unknown"}


def _latest_capture():
    files = [
        os.path.join(CAPTURES_DIR, f)
        for f in os.listdir(CAPTURES_DIR)
        if f.lower().endswith(".jpg")
    ]
    if not files:
        return None
    return max(files, key=os.path.getmtime)


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else _latest_capture()
    if not path:
        print("No captured image found. Run capture.py first, or pass a path.")
        sys.exit(1)

    print(f"Analyzing: {path}")
    result = analyze_image(path)

    if result["parsed"] is not None:
        print(json.dumps(result["parsed"], indent=2))
    else:
        print(f"Failed to parse JSON after retry ({result['error']}). Raw response:")
        print(result["raw_text"])
