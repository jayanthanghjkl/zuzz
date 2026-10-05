"""Occipital Lobe — image helpers.

The two byte-level operations the region needs and nothing else. Both are lazy
imports of Pillow, which is a hard dependency in ``requirements.txt`` but is not
imported at module scope: the region must be importable — and testable — on a
machine with no imaging stack, and an import error at module scope would take the
whole package down rather than the one call that needs it.

Both raise :class:`~brain.occipital_lobe.exceptions.VisionModelUnavailable` rather
than ``ImportError``. A caller asking for a crop does not care whether the reason
it failed was a missing wheel or an unreachable model; the API maps one to a 503
either way, and letting ``ImportError`` through would make it a 500.
"""

from __future__ import annotations

from io import BytesIO
from typing import Optional, Tuple

from brain.occipital_lobe.exceptions import VisionModelUnavailable


def pillow() -> object:
    """Return the ``PIL.Image`` module.

    Returns:
        The imported ``PIL.Image``.

    Raises:
        VisionModelUnavailable: Pillow is not installed.
    """
    try:
        from PIL import Image
    except ImportError as exc:
        raise VisionModelUnavailable(
            "Pillow is not installed - run: pip install Pillow",
            reason="missing_dependency",
        ) from exc
    return Image


def crop(image: bytes, box: Tuple[int, int, int, int]) -> bytes:
    """Crop PNG bytes to ``(x, y, width, height)``.

    Args:
        image: PNG bytes.
        box: The crop rectangle, in the image's own coordinates.

    Returns:
        PNG bytes of the cropped image.
    """
    Image = pillow()
    x, y, width, height = box
    with Image.open(BytesIO(image)) as source:
        cropped = source.crop((x, y, x + width, y + height))
        buffer = BytesIO()
        cropped.save(buffer, format="PNG")
        return buffer.getvalue()


def png_size(data: bytes) -> Optional[Tuple[int, int]]:
    """Return a PNG's pixel size from its header, without decoding it.

    Reads IHDR directly. The layout code only needs dimensions, and a fake backend
    should not pay for decoding a full-screen PNG in order to learn that it is
    full-screen.

    Args:
        data: PNG bytes.

    Returns:
        ``(width, height)``, or None when the header cannot be read — which is the
        honest answer for bytes that are not a PNG, and is treated as "unknown
        size" by every caller rather than as a zero.
    """
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    return (int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big"))


def encode_rgb(pixels: bytes, size: Tuple[int, int]) -> bytes:
    """Encode raw RGB bytes as a PNG.

    Args:
        pixels: ``width * height * 3`` bytes of RGB, row-major.
        size: ``(width, height)``.

    Returns:
        PNG bytes.
    """
    Image = pillow()
    image = Image.frombytes("RGB", size, pixels)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()