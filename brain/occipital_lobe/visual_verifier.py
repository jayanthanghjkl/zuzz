"""Occipital Lobe — comparing two screenshots.

"Did the action work?" is the question a computer-use agent gets wrong most often,
because "the screen looks the same" is not the same as "nothing happened" and
"something changed" is not the same as "the right thing happened". This module
combines the two measurements in :mod:`brain.occipital_lobe.image_fingerprint` into
one honest verdict: **did the screen change at all, and by how much**.

The measures are complementary on purpose. The perceptual hash survives re-encoding
and compositor jitter, so it is what answers "is this the same page". The pixel diff
catches what the hash cannot: a small but total change, such as a spinner replacing a
list, which shifts few gradient comparisons while moving a large fraction of pixels.

Both thresholds are declared constants rather than tuned here. A screen diff is one
input to a decision and the decision is the caller's; this module reports the
measurement and says which of its own thresholds it crossed.

It deliberately does not say whether the change was *the* change. A test that asked
"did the file save?" needs the OCR reader for that, and pretending otherwise is how a
verifier becomes a rubber stamp.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from brain.occipital_lobe import image_fingerprint
from brain.occipital_lobe.exceptions import VisionModelUnavailable

#: Bits of the hash that may differ before the screens count as different. Low on
#: purpose: a click that changed nothing produces a near-zero distance, and the whole
#: point is to notice when the distance is not zero.
HASH_DISTANCE_THRESHOLD = 3

#: Fraction of pixels that may differ before the screens count as different. Above the
#: hash threshold rather than at it, because the hash is the sensitive measure and
#: this one is the blunt one.
DIFF_RATIO_THRESHOLD = 0.002

#: Below this fraction a diff is reported but not treated as evidence of anything, so
#: a report can say "something moved by a hair" without claiming the screen changed.
MIN_MEANINGFUL_DIFF = 0.0001


@dataclass(frozen=True)
class ScreenDiff:
    """How two screenshots differ.

    Attributes:
        changed: Whether the screens differ beyond both thresholds.
        hamming: Bits differing in the perceptual hashes.
        distance: Hash distance as a fraction, 0.0-1.0.
        diff_ratio: Fraction of pixels that differ.
        reason: Which measure crossed its threshold, for the log and the UI.
    """

    changed: bool
    hamming: int = 0
    distance: float = 0.0
    diff_ratio: float = 0.0
    reason: str = "identical"

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable form for the API.

        Returns:
            The verdict, both measurements, and the thresholds they were judged
            against — the last so a report can be read without knowing the constants.
        """
        return {
            "changed": self.changed,
            "hamming": self.hamming,
            "distance": round(self.distance, 4),
            "diff_ratio": round(self.diff_ratio, 6),
            "reason": self.reason,
            "thresholds": {
                "hamming": HASH_DISTANCE_THRESHOLD,
                "diff_ratio": DIFF_RATIO_THRESHOLD,
            },
        }


def compare(
    before: bytes,
    after: bytes,
    hamming_threshold: int = HASH_DISTANCE_THRESHOLD,
    diff_threshold: float = DIFF_RATIO_THRESHOLD,
) -> ScreenDiff:
    """Compare two screenshots.

    Args:
        before: The screenshot taken before the action.
        after: The screenshot taken after it.
        hamming_threshold: Hash distance above which the screens differ.
        diff_threshold: Pixel fraction above which the screens differ.

    Returns:
        The verdict and both measurements.

    Raises:
        VisionModelUnavailable: Pillow is not installed, or an image could not be
            read.
    """
    first = image_fingerprint.perceptual_hash(before)
    second = image_fingerprint.perceptual_hash(after)
    distance_bits = image_fingerprint.hamming(first, second)
    fraction = image_fingerprint.distance(first, second)
    ratio = image_fingerprint.pixel_diff_ratio(before, after)
    hash_changed = distance_bits > hamming_threshold
    pixels_changed = ratio > diff_threshold
    reasons = []
    if hash_changed:
        reasons.append(f"hash differs in {distance_bits} bits")
    if pixels_changed:
        reasons.append(f"{ratio * 100:.2f}% of pixels differ")
    if not reasons and MIN_MEANINGFUL_DIFF > ratio > 0.0:
        reasons.append(f"{ratio * 100:.3f}% of pixels differ, below the reporting floor")
    return ScreenDiff(
        changed=hash_changed or pixels_changed,
        hamming=distance_bits,
        distance=fraction,
        diff_ratio=ratio,
        reason="; ".join(reasons) or "identical",
    )


def verify(before: Any, after: Any, label: str = "") -> Dict[str, Any]:
    """Compare two captures, or two stored paths.

    Args:
        before: A :class:`~brain.occipital_lobe.capture.Capture`, raw PNG bytes, or a
            path to a PNG.
        after: The same.
        label: What the comparison is about, for the report.

    Returns:
        The comparison with the label attached. The frames are not included: a
        verification report carrying both images would be twice the size of the answer
        it reports on.
    """
    return {"label": label, **compare(_image(before), _image(after)).to_dict()}


def _image(value: Any) -> bytes:
    """Return the PNG bytes of a capture, of raw bytes, or of a path.

    Args:
        value: The thing to read.

    Returns:
        The image bytes.

    Raises:
        VisionModelUnavailable: The value was none of those three.
    """
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    image = getattr(value, "image", None)
    if image:
        return bytes(image)
    read = getattr(value, "read_bytes", None)
    if callable(read):
        return read()
    raise VisionModelUnavailable(
        f"cannot compare {type(value).__name__}: expected a capture, image bytes or a path",
        reason="bad_input",
    )