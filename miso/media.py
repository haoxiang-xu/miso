"""
miso.media — helpers for building canonical multimodal content blocks.

Builds provider-agnostic blocks that Broth translates correctly for
OpenAI, Claude, and Ollama — no provider-specific code needed in userland.

Typical usage
-------------
    from miso import media

    png_block = media.from_file("logo.png")
    pdf_block = media.from_file("report.pdf")
    url_block = media.from_url("https://example.com/photo.jpg")

    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "Describe this image."},
            png_block,
        ],
    }]
"""

from __future__ import annotations

import base64
from pathlib import Path

_MIME_BY_SUFFIX: dict[str, str] = {
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif":  "image/gif",
    ".webp": "image/webp",
    ".pdf":  "application/pdf",
}


def from_file(path: str | Path) -> dict:
    """Build a canonical miso content block from a local file.

    Supported formats: ``.png``, ``.jpg`` / ``.jpeg``, ``.gif``, ``.webp``, ``.pdf``

    Broth handles the provider-specific encoding automatically:

    * **OpenAI** images → ``input_image`` block
    * **OpenAI** PDFs   → ``input_file`` block (base64 data URL)
    * **Claude** images → ``image`` block
    * **Claude** PDFs   → ``document`` block
    * **Ollama**        → text-only fallback (no multimodal support)

    Args:
        path: Path to a local image or PDF file.

    Returns:
        A canonical miso content block dict.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file extension is not supported.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"miso.media: file not found — {path}")

    suffix = path.suffix.lower()
    mime = _MIME_BY_SUFFIX.get(suffix)
    if mime is None:
        supported = ", ".join(sorted(_MIME_BY_SUFFIX))
        raise ValueError(
            f"miso.media: unsupported file type '{suffix}'. "
            f"Supported extensions: {supported}"
        )

    data = base64.b64encode(path.read_bytes()).decode("ascii")
    block_type = "pdf" if mime == "application/pdf" else "image"

    return {
        "type": block_type,
        "source": {
            "type": "base64",
            "media_type": mime,
            "data": data,
        },
    }


def from_url(url: str, media_type: str | None = None) -> dict:
    """Build a canonical miso image block from a public URL.

    Args:
        url:        Publicly accessible image URL.
        media_type: Optional MIME type override (e.g. ``"image/png"``).
                    Inferred from the URL extension when omitted.

    Returns:
        A canonical miso content block dict.

    Raises:
        ValueError: If ``url`` is empty.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("miso.media: url must be a non-empty string")

    if media_type is None:
        suffix = Path(url.split("?")[0]).suffix.lower()
        media_type = _MIME_BY_SUFFIX.get(suffix, "image/jpeg")

    return {
        "type": "image",
        "source": {
            "type": "url",
            "url": url,
            "media_type": media_type,
        },
    }
