"""Detect image format from raw bytes (magic numbers).

Used so we never mislabel an image's media type to the API: the browser
camera widget and OpenCV can hand us JPEG or PNG regardless of the file
extension we choose, and the API validates the declared media type against
the actual bytes.
"""

_MEDIA_BY_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


def detect_media_type(data, fallback="image/jpeg"):
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return fallback


def ext_for(data, fallback=".jpg"):
    return _MEDIA_BY_EXT.get(detect_media_type(data, fallback=None), fallback)
