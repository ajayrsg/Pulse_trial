"""Step 1: Webcam capture.

Run directly for a standalone preview/capture window:
    python capture.py

Or import `capture_frame()` from other scripts (e.g. the Streamlit app)
to grab a single frame programmatically without the preview window.
"""

import os
import sys
from datetime import datetime

import cv2

from imgutil import ext_for

CAPTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captures")


def grab_jpeg_bytes(camera_index=0, warmup=4):
    """Grab a single frame from the webcam WITHOUT opening a GUI window.

    Used by the dashboard's live mode. Reads a few warmup frames so the
    camera auto-exposes, then returns the last frame JPEG-encoded. Uses only
    VideoCapture.read() / imencode — no cv2.imshow — so it is safe to call
    from Streamlit's worker thread (imshow requires the macOS main thread).
    """
    cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open webcam at index {camera_index}")
    try:
        frame = None
        for _ in range(max(1, warmup)):
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError("Failed to read frame from webcam")
        ok, buf = cv2.imencode(".jpg", frame)
        if not ok:
            raise RuntimeError("Failed to encode frame")
        return buf.tobytes()
    finally:
        cap.release()


def save_bytes(data, ext=None, save_dir=CAPTURES_DIR):
    """Save raw image bytes to a timestamped file and return its path.

    Used by the dashboard, where the frame comes from Streamlit's in-browser
    camera widget rather than an OpenCV preview window. The extension is
    detected from the actual bytes (the widget returns JPEG regardless of
    what we'd name it), so the saved file's extension is truthful.
    """
    os.makedirs(save_dir, exist_ok=True)
    if ext is None:
        ext = ext_for(data)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(save_dir, f"capture_{timestamp}{ext}")
    with open(path, "wb") as f:
        f.write(data)
    return path


def capture_frame(camera_index=0, save_dir=CAPTURES_DIR):
    """Open the webcam, show a live preview, save one JPEG on SPACE, quit on Q.

    Returns the saved file path, or None if the user quit without capturing.
    """
    os.makedirs(save_dir, exist_ok=True)

    cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open webcam at index {camera_index}")

    saved_path = None
    window_name = "Webcam Preview - SPACE to capture, Q to quit"

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError("Failed to read frame from webcam")

            cv2.imshow(window_name, frame)
            key = cv2.waitKey(1) & 0xFF

            if key == ord(" "):
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"capture_{timestamp}.jpg"
                saved_path = os.path.join(save_dir, filename)
                cv2.imwrite(saved_path, frame)
                print(f"Saved: {saved_path}")
                break
            elif key == ord("q"):
                print("Quit without capturing.")
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()

    return saved_path


if __name__ == "__main__":
    try:
        path = capture_frame()
        if path:
            print(f"Capture complete: {path}")
        sys.exit(0)
    except RuntimeError as e:
        print(f"Error: {e}")
        sys.exit(1)
