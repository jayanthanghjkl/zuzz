"""Occipital Lobe — the Moondream client.

One model, one endpoint, one shape of request. ``VisionClient`` encodes images,
builds the request and hands back text; it never decides what the text means, which
is :mod:`brain.occipital_lobe.element_finder`,
:mod:`brain.occipital_lobe.ocr_reader` and
:mod:`brain.occipital_lobe.visual_classifier`'s job. Keeping the split sharp is what
makes each of those testable with a canned reply.

Every prompt is built by :mod:`brain.occipital_lobe.vision_prompts`, never assembled
here from a caller's string. That is not tidiness: it is the only thing standing
between a screenshot and the reasoning stream, and a client that accepted a raw
prompt would be a way around it that looked entirely reasonable to write.

Two things about the request are worth stating rather than leaving in the code.
**The model is Moondream and only Moondream**: Ollama will happily serve any model
on the box, and a text model handed a screenshot answers with confident fiction that
nothing downstream could detect. **Confidence is not requested and never invented**:
Moondream does not report a calibrated confidence for ``/api/generate``, so callers
derive one from observable evidence and
:mod:`brain.occipital_lobe.thresholds` decides what it means.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence

from brain.occipital_lobe.exceptions import VisionModelUnavailable
from brain.occipital_lobe.untrusted_text import UntrustedText
from brain.occipital_lobe.vision_prompts import build, policy_summary
from brain.occipital_lobe.vision_transport import (
    DEFAULT_HOST,
    DEFAULT_TIMEOUT,
    MAX_IMAGE_BYTES,
    OllamaTransport,
    VisionTransport,
)

#: The vision model. A text-only model on the same runtime would answer a
#: screenshot question with fluent invention and nothing downstream could detect it.
DEFAULT_MODEL = "moondream"

#: Ollama's generate endpoint, which takes images. ``/api/chat`` also works for
#: vision-capable models but reads more naturally with a plain prompt.
ENDPOINT = "/api/generate"


@dataclass(frozen=True)
class VisionReply:
    """One answer from the vision model.

    Attributes:
        text: What the model said, stripped.
        raw: The decoded response, for a caller that wants to see more.
        model: The model that answered.
        prompt: The prompt that was sent, kept so a refusal can be explained and a
            test can assert the isolation preamble was present.
    """

    text: str
    raw: Dict[str, Any]
    model: str = DEFAULT_MODEL
    prompt: str = ""

    @property
    def is_empty(self) -> bool:
        """Return True when the model said nothing usable."""
        return not self.text.strip()

    def as_untrusted(self, source: str = "", confidence: float = 0.0) -> UntrustedText:
        """Return the reply as text that is explicitly marked untrusted.

        Args:
            source: Where it came from, for the audit trail.
            confidence: The confidence the caller derived from the answer.

        Returns:
            The reply wrapped so it cannot be mistaken for an instruction.
        """
        return UntrustedText(
            text=self.text, source=source, confidence=confidence, kind="vision"
        )


class VisionClient:
    """Talks to Moondream on a local Ollama.

    Attributes:
        model: The model name to ask for.
        transport: Where requests go; injectable, and the seam the tests use.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        transport: Optional[VisionTransport] = None,
        host: str = DEFAULT_HOST,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """Configure the client.

        Args:
            model: Ollama model name.
            transport: A transport to use instead of the default HTTP one.
            host: Base URL, used only when no transport is given.
            timeout: Seconds to wait, used only when no transport is given.
        """
        self.model = model
        self.transport = transport if transport is not None else OllamaTransport(host, timeout)

    def encode(self, image: bytes) -> str:
        """Return one image as base64, the form Ollama expects.

        Args:
            image: Raw image bytes.

        Returns:
            The base64 string, without a data-URL prefix. Ollama's JSON schema wants
            the bare payload; the prefix is accepted by some versions and silently
            truncated by others.

        Raises:
            VisionModelUnavailable: The image is too large for the endpoint.
        """
        if len(image) > MAX_IMAGE_BYTES:
            raise VisionModelUnavailable(
                f"image is {len(image):,} bytes, above the {MAX_IMAGE_BYTES:,} limit "
                "for one vision request; capture a region instead of the whole screen",
                reason="image_too_large",
            )
        return base64.b64encode(image).decode("ascii")

    async def ask(
        self,
        task: str,
        images: Sequence[bytes],
        question: str = "",
        context: Optional[UntrustedText] = None,
    ) -> VisionReply:
        """Ask the vision model one question about one or more images.

        Args:
            task: A key of
                :data:`~brain.occipital_lobe.vision_prompts.TEMPLATES`.
            images: Raw image bytes. The first is the subject; the rest are context.
            question: The caller's own words, used by ``find``.
            context: Text read from an earlier screenshot, inserted as fenced data.

        Returns:
            The model's answer.

        Raises:
            VisionModelUnavailable: The model could not be reached, or answered
                with something that was not a reply.
            VisionTimeout: It did not answer in time.
            KeyError: ``task`` is not a known vision task.
        """
        prompt = build(task, question, context)
        payload: Dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "images": [self.encode(image) for image in images],
            "stream": False,
        }
        body = await self.transport.post(ENDPOINT, payload)
        text = str(body.get("response", "") or "").strip()
        if not text:
            raise VisionModelUnavailable(
                f"{self.model} returned an empty answer for a {task} request",
                reason="empty_reply",
            )
        return VisionReply(text=text, raw=body, model=self.model, prompt=prompt)

    async def available(self) -> bool:
        """Return True when the model answers a trivial request.

        Used by the status endpoint so the UI can say "ready" or "not loaded"
        rather than making the user discover it by asking a question.

        Returns:
            True when the model replied. Never raises: a failure to probe is a
            False, because the caller's only question is whether to try.
        """
        try:
            await self.transport.post(ENDPOINT, {"model": self.model, "prompt": "ok", "stream": False})
        except Exception:
            return False
        return True

    def describe_prompts(self) -> Dict[str, Any]:
        """Return the isolation policy, for the status endpoint.

        Returns:
            What :func:`brain.occipital_lobe.vision_prompts.policy_summary` reports,
            so the boundary can be confirmed from the API rather than by reading
            the source.
        """
        return policy_summary()