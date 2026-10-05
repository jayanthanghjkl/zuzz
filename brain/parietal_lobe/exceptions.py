"""Parietal Lobe — refusals.

One hierarchy, so the API maps a refusal onto a status by asking a single question
rather than enumerating classes in five modules. The distinctions that carry weight:

* **The gate's verdict and the platform's silence are different refusals.** A
  :class:`DecisionRefused` means the Prefrontal Cortex said no. A
  :class:`SystemAwarenessUnavailable` means the operating system declined to answer —
  ``pygetwindow`` and the Win32 calls are wrapped because this is the boundary where
  ZUZZ meets an OS that is entitled to refuse, and "I could not tell" must never be
  reported as "there is nothing there". Those two answers send the user to opposite
  places, and conflating them is how an agent ends up confidently reporting an empty
  desktop because one API call raised.
* **A hard refusal has no confirmation path.** Everything here is a statement, not a
  question. Confirming any of it would produce exactly the behaviour the Phase 3 plan
  warns about in §5: an approval that the caller then spends on a different read.

The ordering of the subclasses is the API's status mapping, mirroring
:mod:`brain.occipital_lobe.exceptions`: 403 for the gate, 400 for input the caller got
wrong, 503 for a platform or dependency that is not answering, 409 for a stop.
"""

from __future__ import annotations


class ParietalRefused(Exception):
    """Base of every refusal from :mod:`brain.parietal_lobe`."""


class DecisionRefused(ParietalRefused):
    """No decision, or one that does not permit this call.

    Raised for a missing decision, a decision that does not allow execution, and a
    decision issued for a different category. There is nothing about a system read
    that is worth bypassing this for: the category is ``auto`` at every trust level,
    so the gate is not the obstacle, and a caller with no real decision has skipped
    the audit trail that is the only record of what the agent asked about.
    """


class DecisionReplayRefused(DecisionRefused):
    """The decision is genuine but for another operation.

    A confirmation granted for "which window am I in" must not be spendable on
    "list every process", even though both are read-only and both auto-approve. If
    they were the same decision then the audit log could not say which read was
    approved, and the ``target`` string would be decoration.
    """


class DryRunRefused(ParietalRefused):
    """The decision is a dry run, and a dry run does not read.

    Distinct from :class:`DecisionRefused` because the *answer* is still wanted: a
    dry-run policy screen needs to know that the call would have returned something,
    so this is a refusal to execute rather than a refusal to answer.
    """


class KillSwitchRefused(ParietalRefused):
    """The emergency stop engaged after the decision was issued.

    Checked before the decision's own verdict, so a refusal that happens during a stop
    is reported as a stop rather than as an unconfirmed permission.
    """


class SystemAwarenessUnavailable(ParietalRefused):
    """The platform would not answer a system query.

    Covers a missing ``pygetwindow``, a refused Win32 call, and a ``psutil`` process
    that vanished mid-enumeration. Carries :attr:`detail` naming *what* could not be
    read, because "the active window could not be read" is a different fact from "no
    window has focus", and only one of them is worth retrying.

    Never a silent empty answer. An empty list here would be indistinguishable from a
    genuinely empty desktop, and ZUZZ would go on to infer an activity context from the
    absence of evidence.
    """

    def __init__(self, message: str, detail: str = "") -> None:
        """Build the refusal.

        Args:
            message: What failed, in the user's terms.
            detail: The specific read that failed — ``"active-window"``,
                ``"window-state"``, ``"processes"`` — so a caller can retry the rest.
        """
        super().__init__(message)
        self.detail = detail


class NoSuchMonitor(ParietalRefused):
    """A monitor index was asked for that is not attached.

    A 404 rather than a 400: the request was well formed and the layout is readable,
    the answer genuinely is "that display is not there".
    """


class InvalidContextRefused(ParietalRefused):
    """A caller asked the context detector to judge something it cannot judge.

    Raised when a caller supplies a window record with no title *and* no process name
    and asks for a confident classification. "I have no evidence" is a legitimate
    answer to return (``unknown``), so this exists only for the narrower case where
    somebody demands a confident answer they cannot have — an empty record is not
    silently upgraded to ``idle``, because idle is a claim about the user's
    behaviour and absence of evidence is not evidence of absence.
    """
