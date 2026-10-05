"""Occipital Lobe — image fingerprinting.

The two measurements behind "did this screen change": a perceptual hash and a pixel
diff ratio. Kept apart from :mod:`brain.occipital_lobe.visual_verifier` because they
are pure functions over image bytes, they are the part most worth testing directly,
and the verdict that combines them should not have to be re-read to find out what a
"distance" is.

The hash is **dHash**: the image is reduced to a 9x8 greyscale thumbnail and each
pixel is compared with the one to its right, giving 64 bits of horizontal gradient.
Compared with an average hash it barely moves when a page re-renders identically or
when the capture is re-encoded, which is the failure that matters here — an agent
that reports "the screen changed" because a JPEG artifact shifted a few pixels will
start ignoring its own verifier.
"""

from __future__ import annotations

from io import BytesIO
from typing import Tuple

from brain.occipital_lobe import image_ops

#: Thumbnail size before gradients are compared. Nine columns gives eight
#: comparisons per row across eight rows: 64 bits.
HASH_SIZE = (9, 8)

#: Bits in the hash.
HASH_BITS = HASH_SIZE[0] * HASH_SIZE[1] - HASH_SIZE[1]


def perceptual_hash(image: bytes) -> int:
    """Return the dHash of an image as a 64-bit integer.

    Args:
        image: Raw image bytes.

    Returns:
        The hash. Two images that look the same generally produce the same hash.

    Raises:
        VisionModelUnavailable: Pillow is not installed.
    """
    Image = image_ops.pillow()
    with Image.open(BytesIO(image)) as source:
        small = source.convert("L").resize(HASH_SIZE)
        pixels = list(small.getdata())
    bits = 0
    for row in range(HASH_SIZE[1]):
        offset = row * HASH_SIZE[0]
        for column in range(HASH_SIZE[0] - 1):
            bits = (bits << 1) | (1 if pixels[offset + column] > pixels[offset + column + 1] else 0)
    return bits


def hamming(left: int, right: int) -> int:
    """Return how many bits differ between two hashes.

    Args:
        left: One hash.
        right: The other.

    Returns:
        The bit count; 0 for identical hashes.
    """
    return int(bin(left ^ right).count("1"))


def distance(left: int, right: int) -> float:
    """Return the hash distance as a fraction of the hash width.

    Args:
        left: One hash.
        right: The other.

    Returns:
        A fraction in 0.0-1.0, which is comparable across models and image sizes in
        a way a raw bit count is not.
    """
    return hamming(left, right) / float(HASH_BITS)


def pixel_diff_ratio(before: bytes, after: bytes) -> float:
    """Return the fraction of pixels that differ between two images.

    Args:
        before: The earlier image.
        after: The later image.

    Returns:
        A fraction in 0.0-1.0. Images of different sizes are compared over their
        common area rather than refused: a window resized between two captures is a
        change like any other, and refusing would make the one case a user most
        wants verified the one case that cannot be.
    """
    Image = image_ops.pillow()
    with Image.open(BytesIO(before)) as first, Image.open(BytesIO(after)) as second:
        left = first.convert("RGB")
        right = second.convert("RGB")
        width = min(left.width, right.width)
        height = min(left.height, right.height)
        if width <= 0 or height <= 0:
            return 1.0
        left_pixels = list(left.crop((0, 0, width, height)).getdata())
        right_pixels = list(right.crop((0, 0, width, height)).getdata())
    differing = sum(1 for a, b in zip(left_pixels, right_pixels) if a != b)
    return differing / max(1, len(left_pixels))


def size_of(image: bytes) -> Tuple[int, int]:
    """Return an image's pixel size.

    Args:
        image: Raw image bytes.

    Returns:
        ``(width, height)``, or ``(0, 0)`` when the header could not be read.

    Raises:
        VisionModelUnavailable: Pillow is not installed.
    """
    Image = image_ops.pillow()
    with Image.open(BytesIO(image)) as source:
        return (source.width, source.height)