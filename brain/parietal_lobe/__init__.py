"""Parietal Lobe — where am I, and what is happening right now.

Phase 3 Step 3. The sense of position: which window has focus, what the display looks
like, what is running, where the pointer is, and — as an inference, labelled as one —
what the user appears to be doing.

The region is built around five invariants, each enforced by a module rather than by a
convention someone has to remember:

* **Permission is the signature.** Every public method on
  :class:`~brain.parietal_lobe.parietal_lobe.ParietalLobe` takes a
  :class:`~brain.prefrontal_cortex.permission_decision.PermissionDecision` first, and
  :mod:`brain.parietal_lobe.parietal_guards` checks that it names the exact operation
  about to be run. The ``system_awareness`` trust row is ``auto`` at every level, which
  means the gate does not *ask* — not that nothing is checked.
* **One reader per fact.** The monitor layout is read once, by
  :class:`~brain.parietal_lobe.screen_info.ScreenInfo`, and shared with the mouse and
  workspace readers; the window list is read once, by
  :mod:`body.hands.window_manager`, and consumed rather than re-enumerated. Two readers
  of one fact is how a screenshot of monitor 1 and a click validated against monitor 2
  come to disagree with nothing to arbitrate between them.
* **Silence is not emptiness.** Every reader distinguishes "the OS would not answer"
  from "there is nothing there", and the distinction survives into the return value.
  :class:`~brain.parietal_lobe.exceptions.SystemAwarenessUnavailable` exists because an
  empty list would otherwise be indistinguishable from an empty desktop — and a context
  detector reading "no windows" as "nothing is running" would answer a question about a
  person with a fact about an API call.
* **Inference carries its evidence.** Every
  :class:`~brain.parietal_lobe.context_detector.Context` names the rule that fired, and
  ``idle`` has exactly one source: the OS input-idle timer. Absence of a match is
  ``unknown``, never ``idle``.
* **Titles never enter a decision target.** The target is an operation name, so a
  document title is not pattern-matched by the Amygdala, not written to the audit log
  as a path, and not shown in a confirmation popup.

How the pieces fit:

* :mod:`~brain.parietal_lobe.parietal_lobe` — the orchestrator, and the region the API
  and the tool layer talk to.
* :mod:`~brain.parietal_lobe.parietal_guards` — the decision and monitor checks.
* :mod:`~brain.parietal_lobe.window_tracker` — focused window, minimised state.
* :mod:`~brain.parietal_lobe.screen_info` — monitors, resolution, DPI.
* :mod:`~brain.parietal_lobe.app_tracker` — running applications and their resources.
* :mod:`~brain.parietal_lobe.mouse_tracker` — pointer position and cursor visibility.
* :mod:`~brain.parietal_lobe.workspace_detector` — desktop surfaces.
* :mod:`~brain.parietal_lobe.context_detector` — activity inference, with evidence.
"""

from brain.parietal_lobe.app_tracker import MAX_APPS, AppTracker
from brain.parietal_lobe.context_detector import (
    BROWSING,
    CODING,
    CONTEXTS,
    GAMING,
    IDLE,
    IDLE_SECONDS,
    READING,
    RULES,
    UNKNOWN,
    WRITING,
    Context,
    ContextDetector,
)
from brain.parietal_lobe.exceptions import (
    DecisionRefused,
    DecisionReplayRefused,
    DryRunRefused,
    InvalidContextRefused,
    KillSwitchRefused,
    NoSuchMonitor,
    ParietalRefused,
    SystemAwarenessUnavailable,
)
from brain.parietal_lobe.mouse_tracker import MouseTracker
from brain.parietal_lobe.parietal_guards import OPERATIONS, SYSTEM_AWARENESS
from brain.parietal_lobe.parietal_lobe import ParietalLobe
from brain.parietal_lobe.screen_info import ScreenInfo
from brain.parietal_lobe.window_tracker import TITLE_BUDGET, WindowTracker
from brain.parietal_lobe.workspace_detector import WorkspaceDetector

__all__ = [
    "AppTracker",
    "BROWSING",
    "CODING",
    "CONTEXTS",
    "Context",
    "ContextDetector",
    "DecisionRefused",
    "DecisionReplayRefused",
    "DryRunRefused",
    "GAMING",
    "IDLE",
    "IDLE_SECONDS",
    "InvalidContextRefused",
    "KillSwitchRefused",
    "MAX_APPS",
    "MouseTracker",
    "NoSuchMonitor",
    "OPERATIONS",
    "ParietalLobe",
    "ParietalRefused",
    "READING",
    "RULES",
    "SYSTEM_AWARENESS",
    "ScreenInfo",
    "SystemAwarenessUnavailable",
    "TITLE_BUDGET",
    "UNKNOWN",
    "WRITING",
    "WindowTracker",
    "WorkspaceDetector",
]
