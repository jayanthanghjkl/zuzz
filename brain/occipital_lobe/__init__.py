"""Occipital Lobe — screen vision, element location and OCR.

Phase 3 Step 1. Everything ZUZZ needs to see a screen and act on it: capture through
``mss``, interpretation through Moondream on a local Ollama, coordinates for a click,
text from a region, before/after comparison, and a classifier that decides whether a
frame may be written to disk at all.

The region is built around five invariants, each of which is enforced by a module
rather than by a convention someone has to remember:

* **Permission is the signature.** Every public method takes a
  :class:`~brain.prefrontal_cortex.permission_decision.PermissionDecision` first, and
  :mod:`brain.occipital_lobe.vision_guards` checks that it names the exact operation,
  monitor and region about to be acted on. Mirrors the Body layers, so a screenshot is
  reached the way a file read or a click is.
* **Crops are refused, never clamped.** A box that leaves its monitor raises
  :class:`~brain.occipital_lobe.exceptions.CropOutOfBounds`. A clamped crop is a
  picture of something other than what was asked for, and every coordinate read out
  of it would then be wrong by an amount the caller cannot see.
* **The VRAM budget is consulted before the model loads.**
  :mod:`brain.occipital_lobe.vision_resources` asks the Hypothalamus, in the
  Orchestrator's own words, and a refusal becomes
  :class:`~brain.occipital_lobe.exceptions.VisionModelUnavailable` rather than a
  crash of the GPU driver.
* **Screen text never becomes reasoning.** It arrives as
  :class:`~brain.occipital_lobe.untrusted_text.UntrustedText`, it enters a prompt
  through :meth:`~brain.occipital_lobe.untrusted_text.UntrustedText.as_data`, and
  every prompt is built by :mod:`brain.occipital_lobe.vision_prompts`, which is never
  imported by the Cerebrum and never imports it.
* **Confidence is derived, never invented.** Moondream does not calibrate one, so
  :mod:`brain.occipital_lobe.element_scoring` combines the model's stated number with
  label agreement and reports 0.0 when there is no stated number at all.
  :mod:`brain.occipital_lobe.thresholds` then says what that means for the job, using
  the per-job thresholds approved in ``config/vision_thresholds.json``.

How the pieces fit:

* :mod:`~brain.occipital_lobe.occipital_lobe` — the orchestrator, and the region the
  API talks to.
* :mod:`~brain.occipital_lobe.screen_capture` — the capture engine and the permission
  guard on it.
* :mod:`~brain.occipital_lobe.capture_backend` / :mod:`~brain.occipital_lobe.mss_backend`
  — the seam to the screen, with a static fake for tests and dry-run previews.
* :mod:`~brain.occipital_lobe.screenshot_store` — unique names, a self-ignoring
  directory, and no traversal on the way back in.
* :mod:`~brain.occipital_lobe.vision_client` / :mod:`~brain.occipital_lobe.vision_transport`
  — Moondream over Ollama, and the seam to it.
* :mod:`~brain.occipital_lobe.element_finder` / :mod:`~brain.occipital_lobe.box_parser`
  / :mod:`~brain.occipital_lobe.box_validation` / :mod:`~brain.occipital_lobe.element_scoring`
  — "find the Search bar" to a coordinate, in pixel, normalised and desktop space.
* :mod:`~brain.occipital_lobe.ocr_reader` / :mod:`~brain.occipital_lobe.ocr_engines` —
  text from a region, with the approved Tesseract fallback ladder.
* :mod:`~brain.occipital_lobe.visual_verifier` / :mod:`~brain.occipital_lobe.image_fingerprint`
  — did the screen change, on two independent measures.
* :mod:`~brain.occipital_lobe.visual_classifier` / :mod:`~brain.occipital_lobe.sensitivity_signals`
  — password boxes, API keys and masked fields, before storage.
* :mod:`~brain.occipital_lobe.storage_guard` — the one path to disk.
"""

from brain.occipital_lobe.box_parser import ParsedBox, parse_box
from brain.occipital_lobe.box_validation import BackwardsBox
from brain.occipital_lobe.capture import Capture
from brain.occipital_lobe.capture_backend import CaptureBackend, StaticBackend
from brain.occipital_lobe.element_finder import ElementFinder
from brain.occipital_lobe.exceptions import (
    CropOutOfBounds,
    DecisionRefused,
    DecisionReplayRefused,
    DryRunRefused,
    ElementNotFound,
    KillSwitchRefused,
    MonitorNotFound,
    OcrUnavailable,
    ProtectedRefused,
    SensitiveScreenDetected,
    VisionModelUnavailable,
    VisionRefused,
    VisionTimeout,
)
from brain.occipital_lobe.lobe_status import build as build_status
from brain.occipital_lobe.lobe_vision import LobeVision
from brain.occipital_lobe.mss_backend import MssBackend
from brain.occipital_lobe.occipital_lobe import OccipitalLobe
from brain.occipital_lobe.ocr_reader import OcrReader
from brain.occipital_lobe.screenshot_store import Screenshots
from brain.occipital_lobe.screen_capture import ScreenCapture
from brain.occipital_lobe.sensitivity_index import SensitivityIndex
from brain.occipital_lobe.storage_guard import StorageGuard
from brain.occipital_lobe.untrusted_text import UntrustedText
from brain.occipital_lobe.visual_classifier import CAUTION, SAFE, SENSITIVE, VisualClassifier
from brain.occipital_lobe.visual_verifier import ScreenDiff, compare, verify
from brain.occipital_lobe.vision_client import VisionClient, VisionReply
from brain.occipital_lobe.vision_transport import (
    OllamaTransport,
    ScriptedTransport,
    VisionTransport,
)

__all__ = [
    "BackwardsBox",
    "CAUTION",
    "Capture",
    "CaptureBackend",
    "CropOutOfBounds",
    "DecisionRefused",
    "DecisionReplayRefused",
    "DryRunRefused",
    "ElementFinder",
    "ElementNotFound",
    "KillSwitchRefused",
    "LobeVision",
    "MonitorNotFound",
    "MssBackend",
    "OcrReader",
    "OcrUnavailable",
    "OccipitalLobe",
    "ParsedBox",
    "ProtectedRefused",
    "SAFE",
    "SENSITIVE",
    "ScreenCapture",
    "ScreenDiff",
    "Screenshots",
    "ScriptedTransport",
    "SensitivityIndex",
    "SensitiveScreenDetected",
    "StaticBackend",
    "StorageGuard",
    "UntrustedText",
    "VisionClient",
    "VisionModelUnavailable",
    "VisionRefused",
    "VisionReply",
    "VisionTimeout",
    "VisionTransport",
    "OllamaTransport",
    "build_status",
    "compare",
    "parse_box",
    "verify",
]