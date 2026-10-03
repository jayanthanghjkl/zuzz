"""Live verification for the Body/Hands layer (docs/BODY_HANDS.md, section 8).

Nothing in the automated suite can move a real cursor: every test drives
a RecordingActuator, so the suite proves refusals but cannot prove that an
allowed action actually reaches hardware. This script is that missing proof,
and it is the only place in the project that deliberately sends input to the
machine it runs on. That is why it is interactive rather than automatic:

* A human decides, per step, whether the action happens. Every step prints
  what is about to occur and what it changes on the machine, then waits for
  exactly ``GO`` on a line. Any other input skips the step — when the screen
  does not look right, skipping is the correct answer, and the skip is
  recorded like any other outcome.
* Three seconds pause between steps, so each effect stays watchable.
* Ctrl+C is not an exception path but the emergency stop itself: it
  triggers the real kill switch (the same on-disk flag the running
  assistant reads), releases every held key, and prints how to recover.
  The handler is written so that it cannot itself raise halfway through
  and leave a key held.

The six phases run in a fixed order: safe mouse movement with no clicks, a
scratch Notepad that the run never closes, the Ctrl+Alt+Del refusal (the one
assertion a mock cannot make, because only a real keyboard proves no key
reached Windows), a deliberate watchdog trip that engages the stop for real,
the reset of that stop with the word the policy demands, and a clean shutdown
that returns the cursor, clears the clipboard and writes the audit entry.

Run it from the repo root, with the ZUZZ server already running::

    python scripts/live_verify_hands.py

Every step appends one JSONL line to
``storage/logs/live_verify_<timestamp>.jsonl``
and screenshots go to ``storage/logs/live_verify_<timestamp>/``. Exit code 0
means the run completed with every assertion passing, 2 means it completed
with an assertion failing, and 1 means it never completed.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from brain.brainstem.activity_logger import get_logger
from brain.prefrontal_cortex.action import Action, ActionCategory
from brain.prefrontal_cortex.permission_decision import PermissionDecision
from brain.prefrontal_cortex.permission_gate import PermissionGate
from brain.prefrontal_cortex.trust_manager import TrustManager
from brain.spinal_cord.kill_switch import ResetRefused, get_kill_switch
from brain.spinal_cord.reset_policy import RESET_WORD
from brain.spinal_cord.stop_state import COOLDOWN_LADDER, SOURCE_UI
from brain.spinal_cord.watchdog import Watchdog
from core.audit import KILL_SWITCH, get_audit_log
from core.config import get_settings

from body.hands import action_classifier
from body.hands.exceptions import (
    DangerousHotkeyRefused,
    HandsRefused,
    KillSwitchRefused,
)
from body.hands.hands import Hands
from body.hands.mouse import coordinate_target

#: The only answer that runs a step. Everything else skips it.
GO = "GO"

#: Seconds between steps, so each effect is watchable on the real screen.
PAUSE_BETWEEN_STEPS_SECONDS = 3.0

#: The string Phase B types into the scratch Notepad.
TYPE_TEXT = "ZUZZ live verification test"

#: The secure-attention sequence Phase C asks for and must never send.
DANGEROUS_COMBO = ["ctrl", "alt", "delete"]

#: How many clicks the watchdog burst attempts before giving up on a trip.
BURST_CLICKS = 60

#: Seconds between clicks inside the burst. Small on purpose: the watchdog's
#: click signal is a rate (50 clicks in 10s), so the burst has to fit inside
#: the window for the trip to be observable.
BURST_INTERVAL = 0.02

#: Poll the watchdog this often during the burst, so the trip is detected
#: within a few clicks of happening rather than after all sixty.
WATCHDOG_POLL_EVERY = 5

#: The lowest trust level at which this run's input actions auto-approve.
#: Below it the gate answers "ask", which an in-process runner cannot
#: answer, so the run refuses to start rather than fail every step.
MINIMUM_TRUST_LEVEL = 3

STORAGE_LOGS = ROOT / "storage" / "logs"

# Callables the generic step runner accepts.
Decider = Callable[[Any], PermissionDecision]
Actor = Callable[[Optional[PermissionDecision], Any, str], Any]
Checker = Callable[[Any], Optional[str]]


class LiveVerify:
    """The interactive Body/Hands verification runner.

    Attributes:
        kill_switch: The process-wide switch, shared with the running
            assistant through the on-disk flag. Sharing it is the point:
            a trip here must stop the assistant, and a reset here must
            re-arm it.
        logger: The real activity logger, so the watchdog counts what
            this run does instead of what a test double recorded.
        hands: The real facade, built on the PyAutoGUI actuator.
        watchdog: A watchdog over the same switch and log, so Phase D
            trips the real stop the way the scheduled one would.
    """

    def __init__(self) -> None:
        """Build the runner against the real collaborators.

        Raises:
            ActuatorError: PyAutoGUI cannot reach a display, which on a
                headless machine is the correct answer rather than a
                crash: there is no hardware to verify against.
        """
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        self.log_path = STORAGE_LOGS / f"live_verify_{stamp}.jsonl"
        self.shot_dir = STORAGE_LOGS / f"live_verify_{stamp}"
        self.records: List[Dict[str, Any]] = []
        self.assertions_checked = 0
        self.assertions_failed = 0
        self.aborted = False
        self.notepad_open = False
        self.origin: Optional[Tuple[int, int]] = None
        self._centre: Optional[Tuple[int, int]] = None
        self.current_step = ""
        self.kill_switch = get_kill_switch()
        self.logger = get_logger()
        self.audit = get_audit_log()
        self.gate = PermissionGate()
        self.trust = TrustManager()
        self.trust_level = self.trust.current()
        self.hands = Hands(kill_switch=self.kill_switch, logger=self.logger)
        self.watchdog = Watchdog(switch=self.kill_switch, logger=self.logger)

    # ------------------------------------------------------------------
    # Startup guards
    # ------------------------------------------------------------------

    def base_url(self) -> str:
        """Return the URL the running assistant listens on.

        The address comes from the same settings the server reads, so the
        guard below probes the assistant this script was meant to watch
        rather than a guessed port.
        """
        settings = get_settings()
        host = str(settings.get("host", "127.0.0.1"))
        port = int(settings.get("port", 8080))
        return f"http://{host}:{port}"

    def check_server(self) -> bool:
        """Confirm the ZUZZ server is running before anything moves.

        The run shares state with the assistant — the trust level file,
        the activity log, the audit log and the stop flag — so a run
        without the server would verify a machine the assistant is not
        driving. The check is a plain read of the safety endpoint.

        Returns:
            True when the server answered.
        """
        url = f"{self.base_url()}/api/safety/trust"
        print(f"ZUZZ server: probing {url}")
        try:
            import httpx

            response = httpx.get(url, timeout=3.0)
        except ImportError:
            print("httpx is missing. Install it with: pip install -r requirements.txt")
            return False
        except Exception as exc:
            print(f"cannot reach the ZUZZ server ({type(exc).__name__}).")
            print("Start it first, in its own terminal, from the repo root:")
            print("    python start.py")
            print("Then re-run this script (docs/BODY_HANDS.md, section 8.1).")
            return False
        if response.status_code != 200:
            print(f"the ZUZZ server answered HTTP {response.status_code} at {url}.")
            print("It is not healthy enough to verify against; see the server")
            print("terminal for the error, then re-run this script.")
            return False
        print(f"ZUZZ server is running at {self.base_url()}")
        print(f"trust level reported by the server: {response.json().get('current')}")
        return True

    def check_switch_disengaged(self) -> bool:
        """Refuse to start on top of an already-engaged stop.

        A stop left behind by a previous run (or by a server restart that
        read the persisted flag) would make every step a refusal, which
        proves nothing about the hardware.

        Returns:
            True when the switch is disengaged.
        """
        if not self.kill_switch.is_killed():
            print("kill switch: disengaged")
            return True
        status = self.kill_switch.status()
        print("kill switch: ENGAGED — ZUZZ is stopped.")
        print(f"  reason: {status['reason']}")
        print("Reset it before verifying (docs/BODY_HANDS.md, section 8.6):")
        print(f"    {self.reset_command()}")
        return False

    def check_trust_level(self) -> bool:
        """Confirm the trust level lets this run's actions auto-approve.

        Returns:
            True when the level is at least ``MINIMUM_TRUST_LEVEL``.
        """
        print(f"trust level (storage/trust_level.json): {self.trust_level}")
        if self.trust_level >= MINIMUM_TRUST_LEVEL:
            return True
        print(f"this run needs trust level {MINIMUM_TRUST_LEVEL} or higher:")
        print("its mouse and keyboard actions are auto-approved from level")
        print(f"{MINIMUM_TRUST_LEVEL}, and an in-process runner cannot answer")
        print("the confirmation a lower level demands. Raise it in the web UI")
        print("(Safety tab) or with:")
        print(f"    {self.trust_command(self.trust_level + 1)}")
        print("See docs/BODY_HANDS.md, sections 8.1 and 8.6.")
        return False

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------

    def decide(self, category: ActionCategory, target: str) -> PermissionDecision:
        """Ask the real permission gate for a real decision.

        The gate is the only source of decisions: forging one would make
        the Body's second check meaningless, which is the exact hole the
        facade exists to close. No threat assessment is supplied, so the
        Amygdala scores the action the same way the API path does.

        Args:
            category: The category the operation maps onto.
            target: The resolved target the Body will act on, in the form
                the Body will use — a coordinate as ``"x,y"``, an
                application as its resolved executable.

        Returns:
            The decision, which the Body then verifies against the same
            category and target.
        """
        action = Action(category=category, target=target)
        return self.gate.decide(action, self.trust_level)

    # ------------------------------------------------------------------
    # Console and log plumbing
    # ------------------------------------------------------------------

    def pause(self) -> None:
        """Wait so the effect of the last action stays watchable."""
        time.sleep(PAUSE_BETWEEN_STEPS_SECONDS)

    def ask(self, word: str, ignore_case: bool = False) -> Optional[str]:
        """Collect the go-ahead for one step.

        Args:
            word: The exact word that runs the step.
            ignore_case: True when the policy the word feeds accepts
                either case, as the reset word does.

        Returns:
            The typed word when it matched, else None — and None means
            skip, which is the safe answer to every ambiguity.
        """
        prompt = f"Type {word} to proceed (anything else skips this step): "
        try:
            answer = input(prompt)
        except EOFError:
            return None
        answer = answer.strip()
        if ignore_case:
            return answer if answer.upper() == word.upper() else None
        return answer if answer == word else None

    def record(
        self,
        phase: str,
        name: str,
        outcome: str,
        decision: Optional[PermissionDecision] = None,
        detail: Any = None,
        refusal_reason: Optional[Exception] = None,
        error: Optional[Exception] = None,
        summary: str = "",
    ) -> None:
        """Append one step to the in-memory list and the JSONL log.

        The log is the review artefact: it carries the real decision the
        gate issued, so an auditor can see what was asked, what was
        allowed, and what the Body answered — without re-running anything
        on the machine.

        Args:
            phase: The phase letter, ``A`` through ``F``.
            name: The step's short name.
            outcome: ``ok``, ``refused``, ``skipped`` or ``error``.
            decision: The decision the step acted on, if it got one.
            detail: Structured result detail, when any.
            refusal_reason: The refusal, when the outcome is refused.
            error: The exception, when the outcome is error.
            summary: The one-line console description of the step.
        """
        entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "phase": phase,
            "step": name,
            "decision": decision.to_dict() if decision is not None else None,
            "outcome": outcome,
        }
        if detail is not None:
            entry["detail"] = detail
        if refusal_reason is not None:
            entry["refusal_reason"] = (
                f"{type(refusal_reason).__name__}: {refusal_reason}"
            )
        if error is not None:
            entry["error"] = f"{type(error).__name__}: {error}"
        if summary:
            entry["summary"] = summary
        self.records.append(entry)
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            print(f"  (could not write the JSONL log: {exc})")

    def screenshot(self, name: str) -> None:
        """Save a PNG of the screen into this run's directory.

        The directory is created on first use, so a run that never
        reaches a screenshot moment leaves nothing behind. A failed
        screenshot is reported and ignored: it is evidence, not an
        action, and must never stop the verification.

        Args:
            name: The file name, without extension.
        """
        try:
            import pyautogui

            self.shot_dir.mkdir(parents=True, exist_ok=True)
            path = self.shot_dir / f"{name}.png"
            pyautogui.screenshot().save(str(path))
            print(f"  screenshot: {path}")
        except ImportError:
            print("  (no screenshot: pyautogui is not installed)")
        except Exception as exc:
            print(f"  (screenshot failed: {exc})")

    # ------------------------------------------------------------------
    # The generic step runner
    # ------------------------------------------------------------------

    def begin(
        self,
        phase: str,
        index: int,
        total: int,
        name: str,
        about_to: str,
        consequence: str,
        decision: Optional[PermissionDecision],
    ) -> None:
        """Print what a step is about to do, before it may do it.

        Args:
            phase: The phase letter.
            index: The step's number within its phase.
            total: The number of steps in the phase.
            name: The step's short name.
            about_to: One line in plain language.
            consequence: What the step changes on the machine.
            decision: The decision the step will act on, if it has one.
        """
        print()
        print(f"[Phase {phase}] step {index}/{total}: {name}")
        print(f"ABOUT TO: {about_to}")
        print(f"CONSEQUENCE: {consequence}")
        if decision is not None:
            payload = decision.to_dict()
            print(
                f"DECISION: {payload['category']} "
                f"target={payload['target']!r} "
                f"trust_level={payload['trust_level']} "
                f"-> {payload['decision']}: {payload['reason']}"
            )

    def run_step(
        self,
        phase: str,
        index: int,
        total: int,
        name: str,
        about_to: str,
        consequence: str,
        act: Actor,
        decide: Optional[Decider] = None,
        pre: Optional[Callable[[], Any]] = None,
        expect: Optional[type] = None,
        check: Optional[Checker] = None,
        describe: Optional[Callable[[Any], str]] = None,
        confirm_word: str = GO,
        ignore_case: bool = False,
        pass_line: str = "",
        abort_on_check_fail: bool = False,
        shot: Optional[str] = None,
    ) -> bool:
        """Run one verification step and say what happened.

        The order inside is the safety argument: resolve the target, then
        obtain the decision, then show both to the human, then wait for
        the go-ahead, then act. A step that cannot resolve what it would
        touch stops the run instead of prompting, because later steps
        depend on the ones before them landing where they were meant to.

        Args:
            phase: The phase letter.
            index: The step's number within its phase.
            total: The number of steps in the phase.
            name: The step's short name.
            about_to: One line in plain language.
            consequence: What the step changes on the machine.
            act: The action, invoked with the decision, the pre-resolution
                result and the typed answer.
            decide: Builds the decision from the pre-resolution result.
            pre: Resolves the target before the prompt, so the human sees
                the real target and a failed resolution never prompts.
            expect: An exception class that counts as the step passing.
            check: Assertion over the result, returning a failure
                description or None.
            describe: One-line console description of the result.
            confirm_word: The word that runs the step.
            ignore_case: Whether the confirm word matches either case.
            pass_line: Printed after an expected refusal, naming what
                the refusal proved.
            abort_on_check_fail: True when a failed assertion means the
                machine is in a state the run must not continue in.
            shot: Screenshot file name to capture after the step acts.

        Returns:
            True when the run should continue, False when it should stop.
        """
        self.current_step = name
        info: Any = None
        decision: Optional[PermissionDecision] = None
        if pre is not None:
            try:
                info = pre()
            except HandsRefused as exc:
                return self._stop_before_acting(phase, index, total, name, exc)
            except Exception as exc:
                return self._abort(phase, name, exc)
        if decide is not None:
            try:
                decision = decide(info)
            except Exception as exc:
                return self._abort(phase, name, exc)
        self.begin(phase, index, total, name, about_to, consequence, decision)
        answer = self.ask(confirm_word, ignore_case)
        if answer is None:
            self.record(
                phase, name, "skipped", decision, summary="skipped by the user"
            )
            print("SKIPPED — nothing was sent to the machine.")
            self.pause()
            return True
        self.pause()
        try:
            result = act(decision, info, answer)
        except Exception as exc:
            if expect is not None and isinstance(exc, expect):
                return self._expected_refusal(
                    phase, name, decision, exc, pass_line, shot
                )
            if isinstance(exc, ResetRefused):
                return self._reset_refused(phase, name, decision, exc)
            if isinstance(exc, KillSwitchRefused):
                return self._stop_engaged(phase, name, decision, exc)
            if isinstance(exc, HandsRefused):
                return self._refused(phase, name, decision, exc)
            return self._abort(phase, name, exc)
        self.assertions_checked += 1 if check is not None else 0
        assertion = check(result) if check is not None else None
        detail = result if isinstance(result, dict) else {"result": result}
        if assertion is not None:
            self.assertions_failed += 1
            self.record(
                phase,
                name,
                "ok",
                decision,
                detail={**detail, "assertion": f"fail: {assertion}"},
                summary=f"ASSERTION FAILED — {assertion}",
            )
            print(f"  ASSERTION FAILED: {assertion}")
            if abort_on_check_fail:
                print("  CRITICAL: this is the failure the run exists to")
                print("  catch. The run stops so nothing else is sent.")
                self.aborted = True
                return False
            print("  The machine is safe; the run continues so the log")
            print("  shows every step, not just the passing ones.")
            self.pause()
            return True
        line = describe(result) if describe else "done"
        self.record(phase, name, "ok", decision, detail=detail,
                    summary=line)
        print(f"RESULT: ok — {line}")
        if shot is not None:
            self.screenshot(shot)
        self.pause()
        return True

    # -- the per-outcome paths the runner takes ------------------------

    def _expected_refusal(
        self,
        phase: str,
        name: str,
        decision: Optional[PermissionDecision],
        exc: Exception,
        pass_line: str,
        shot: Optional[str],
    ) -> bool:
        """Record a refusal that is the step's pass condition."""
        self.assertions_checked += 1
        detail = {"expected": True}
        self.record(
            phase,
            name,
            "refused",
            decision,
            detail=detail,
            refusal_reason=exc,
            summary=f"refused, as it must be — {exc}",
        )
        print(f"RESULT: refused (expected) — {type(exc).__name__}: {exc}")
        print(f"PASS: {pass_line or 'the expected refusal fired.'}")
        if shot is not None:
            self.screenshot(shot)
        self.pause()
        return True

    def _reset_refused(
        self,
        phase: str,
        name: str,
        decision: Optional[PermissionDecision],
        exc: ResetRefused,
    ) -> bool:
        """Record a reset the policy declined, and say how to retry."""
        self.record(
            phase, name, "refused", decision, refusal_reason=exc,
            summary=f"refused — {exc}",
        )
        print(f"RESULT: refused — {type(exc).__name__}: {exc}")
        print("The stop stays engaged. Read the message: a wrong word means")
        print(f"type exactly {RESET_WORD}; a cooldown means wait the stated")
        print("seconds and let the run reach this step again, or reset by")
        print("hand (docs/BODY_HANDS.md, section 8.4). The run continues so")
        print("you can see what a stopped machine looks like.")
        self.pause()
        return True

    def _stop_engaged(
        self,
        phase: str,
        name: str,
        decision: Optional[PermissionDecision],
        exc: KillSwitchRefused,
    ) -> bool:
        """Stop the run when an unexpected step hits an engaged stop."""
        self.record(
            phase, name, "refused", decision, refusal_reason=exc,
            summary="refused — the emergency stop is engaged",
        )
        print(f"RESULT: refused — {type(exc).__name__}: {exc}")
        print("The emergency stop is engaged, so every action is refused")
        print("until it is reset. The run stops here; recover with:")
        print(f"    {self.reset_command()}")
        self.aborted = True
        self.pause()
        return False

    def _refused(
        self,
        phase: str,
        name: str,
        decision: Optional[PermissionDecision],
        exc: HandsRefused,
    ) -> bool:
        """Record an ordinary Body refusal and keep going.

        A refusal means nothing was sent, so the machine is exactly where
        it was — the honest record is the refusal itself.
        """
        self.record(
            phase, name, "refused", decision, refusal_reason=exc,
            summary=f"refused — {exc}",
        )
        print(f"RESULT: refused — {type(exc).__name__}: {exc}")
        print("STATE: nothing was sent to the machine, so it is unchanged.")
        print("The run continues; the refusal is recorded like any outcome.")
        self.pause()
        return True

    def _abort(
        self,
        phase: str,
        name: str,
        exc: Exception,
        decision: Optional[PermissionDecision] = None,
    ) -> bool:
        """Record an unexpected exception, release keys, stop the run."""
        self.record(
            phase, name, "error", decision, error=exc,
            summary=f"error — {type(exc).__name__}: {exc}",
        )
        print(f"RESULT: error — {type(exc).__name__}: {exc}")
        print("STATE: the step raised an unexpected exception. Whether any")
        print("input was sent depends on where it failed, so the run now")
        print("releases every held key and stops rather than let another")
        print("step run in an unknown state.")
        self._release_keys_quietly()
        self.aborted = True
        self.pause()
        return False

    def _stop_before_acting(
        self,
        phase: str,
        index: int,
        total: int,
        name: str,
        exc: HandsRefused,
    ) -> bool:
        """Stop when a target cannot be resolved before the prompt."""
        self.record(
            phase, name, "refused", None, refusal_reason=exc,
            summary=f"refused before it ran — {exc}",
        )
        print(f"[Phase {phase}] step {index}/{total}: {name}")
        print(f"RESULT: refused before it ran — {type(exc).__name__}: {exc}")
        print("The step could not resolve what it would act on, so nothing")
        print("was sent and no prompt appeared. Later steps depend on this")
        print("one having landed where it was meant to, so the run stops.")
        self.aborted = True
        self.pause()
        return False

    def _release_keys_quietly(self) -> None:
        """Release every held key, never letting cleanup itself raise."""
        try:
            self.hands.release_held_keys()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Phase A — safe mouse, no clicks
    # ------------------------------------------------------------------

    def centre(self) -> Tuple[int, int]:
        """Return the centre of the primary display, computed once."""
        if self._centre is None:
            self._centre = (
                self.hands.screens.width // 2,
                self.hands.screens.height // 2,
            )
        return self._centre

    def _record_origin(self, decision: PermissionDecision) -> Tuple[int, int]:
        """Read the cursor and remember it as the origin for the run.

        Args:
            decision: The read decision the read was verified against.

        Returns:
            The ``(x, y)`` position, which Phase F returns the cursor to.
        """
        position = self.hands.get_position(decision)
        self.origin = position
        return position

    def phase_a(self) -> bool:
        """Run Phase A: move the real cursor and bring it back.

        Returns:
            True when the run should continue.
        """
        total = 4
        if not self.run_step(
            "A",
            1,
            total,
            "report-start-position",
            about_to="Read the cursor position through the permission gate.",
            consequence=(
                "Nothing moves and nothing is typed. The position is "
                "recorded as the origin that Phase F returns the cursor to."
            ),
            decide=lambda info: self.decide(ActionCategory.READ_FILES, ""),
            act=lambda decision, info, answer: self._record_origin(decision),
            describe=lambda r: f"cursor at ({r[0]}, {r[1]})",
        ):
            return False
        centre_x, centre_y = self.centre()
        if not self.run_step(
            "A",
            2,
            total,
            "move-to-centre",
            about_to=(
                f"Move the real cursor to the centre of the primary "
                f"display ({centre_x}, {centre_y}) over 1 second."
            ),
            consequence=(
                "The cursor visibly travels across the screen. This is the "
                "live-hardware check: a recording actuator can prove the "
                "command was issued but not that a real pointer moved."
            ),
            decide=lambda info: self.decide(
                ActionCategory.MOUSE_KEYBOARD, coordinate_target(centre_x, centre_y)
            ),
            act=lambda decision, info, answer: self.hands.move(
                decision, centre_x, centre_y, duration=1.0
            ),
            describe=lambda r: f"cursor at ({r['x']}, {r['y']}) "
            f"on monitor {r['monitor']['index']}",
            shot="phase-a-centre",
        ):
            return False
        if self.origin is None:
            return False
        origin_x, origin_y = self.origin
        if not self.run_step(
            "A",
            3,
            total,
            "return-to-origin",
            about_to=(
                f"Move the real cursor back to where Phase A found it "
                f"({origin_x}, {origin_y})."
            ),
            consequence=(
                "The cursor returns to its starting point, so the run "
                "leaves the pointer where you had it."
            ),
            decide=lambda info: self.decide(
                ActionCategory.MOUSE_KEYBOARD, coordinate_target(origin_x, origin_y)
            ),
            act=lambda decision, info, answer: self.hands.move(
                decision, origin_x, origin_y, duration=0.5
            ),
            describe=lambda r: f"cursor at ({r['x']}, {r['y']}) "
            f"on monitor {r['monitor']['index']}",
        ):
            return False
        if not self.run_step(
            "A",
            4,
            total,
            "confirm-returned-position",
            about_to="Read the cursor position a second time.",
            consequence=(
                "The position is compared with the origin from step 1. A "
                "match proves the move out and back both happened on the "
                "real hardware; a mismatch means the pointer did not "
                "return and the assertion fails."
            ),
            decide=lambda info: self.decide(ActionCategory.READ_FILES, ""),
            act=lambda decision, info, answer: self.hands.get_position(decision),
            check=lambda r: None
            if (r[0], r[1]) == self.origin
            else (
                f"the cursor ended at ({r[0]}, {r[1]}); the origin was "
                f"{self.origin}"
            ),
            describe=lambda r: f"cursor at ({r[0]}, {r[1]})",
        ):
            return False
        return True

    # ------------------------------------------------------------------
    # Phase B — scratch Notepad
    # ------------------------------------------------------------------

    def phase_b(self) -> bool:
        """Run Phase B: open Notepad, focus it, type, and leave it open.

        Returns:
            True when the run should continue.
        """
        total = 4
        if not self.run_step(
            "B",
            1,
            total,
            "open-notepad",
            about_to="Launch Notepad (notepad.exe) through the application whitelist.",
            consequence=(
                "A real Notepad window opens. The gate's verdict is printed "
                "above: at trust level 3+ the whitelist auto-approves "
                "notepad.exe and no confirmation popup appears, while at "
                "level 1-2 the gate answers ask and this step is refused — "
                "which is why the run requires level 3+."
            ),
            pre=lambda: action_classifier.resolve_target("app", "notepad"),
            decide=lambda info: self.decide(
                ActionCategory(info["category"]), info["executable"]
            ),
            act=lambda decision, info, answer: self._open_notepad(decision, info),
            describe=lambda r: f"launched {r['executable']} (pid {r['pid']})",
        ):
            return False
        if not self.run_step(
            "B",
            2,
            total,
            "focus-notepad",
            about_to="Bring the Notepad window to the foreground.",
            consequence=(
                "Notepad becomes the focused window, so the next step's "
                "typing lands in it rather than wherever else has focus."
            ),
            pre=lambda: self.hands.windows.resolve("Notepad"),
            decide=lambda info: self.decide(
                ActionCategory.MOUSE_KEYBOARD, str(info["title"])
            ),
            act=lambda decision, info, answer: self.hands.focus_window(
                decision, "Notepad"
            ),
            describe=lambda r: f"focused={r['focused']} ({r['window']['title']})",
        ):
            return False
        if not self.run_step(
            "B",
            3,
            total,
            "type-verification-string",
            about_to=f"Type the literal text '{TYPE_TEXT}' into the focused window.",
            consequence=(
                "The text appears in Notepad, one character at a time. The "
                "string is scanned before it is sent; it carries no dangerous "
                "pattern, so it passes. Nothing else is typed."
            ),
            pre=lambda: TYPE_TEXT,
            decide=lambda info: self.decide(ActionCategory.MOUSE_KEYBOARD, info),
            act=lambda decision, info, answer: self.hands.type_text(
                decision, info, interval=0.01
            ),
            describe=lambda r: f"typed {r['typed']} characters",
        ):
            return False
        if not self.run_step(
            "B",
            4,
            total,
            "confirm-text-appeared",
            about_to=(
                "Ask you to look at the Notepad window and confirm the text "
                "is there. Nothing touches the machine in this step."
            ),
            consequence=(
                "Your answer is recorded in the JSONL log. The run never "
                "closes Notepad — the text is the thing being verified, so "
                "closing it is left to you."
            ),
            act=lambda decision, info, answer: {"confirmed": True, "text": TYPE_TEXT},
            describe=lambda r: f"you confirmed seeing '{r['text']}' in Notepad",
            shot="phase-b-notepad",
        ):
            return False
        print()
        print("Phase B complete. Notepad is left open on purpose: the text you")
        print("verified is still in it. Close it yourself when you are done —")
        print("this run never closes it, because closing can lose the very")
        print("thing the phase exists to show you.")
        return True

    def _open_notepad(
        self, decision: PermissionDecision, resolved: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Launch Notepad and remember that the scratch window exists.

        Args:
            decision: The ``open_apps`` decision for ``notepad.exe``.
            resolved: The whitelist resolution from the pre-step.

        Returns:
            The launch record, including the process id.
        """
        result = self.hands.open_app(decision, resolved["requested"])
        self.notepad_open = True
        return result

    # ------------------------------------------------------------------
    # Phase C — dangerous hotkey refusal
    # ------------------------------------------------------------------

    def phase_c(self) -> bool:
        """Run Phase C: attempt Ctrl+Alt+Del and prove it never left.

        Returns:
            True when the run should continue.
        """
        total = 2
        if not self.run_step(
            "C",
            1,
            total,
            "attempt-ctrl-alt-del",
            about_to=(
                "Ask the Body to press Ctrl+Alt+Del — the secure-attention "
                "sequence, reserved by the hotkey blocklist."
            ),
            consequence=(
                "The blocklist must refuse this BEFORE any key is sent. No "
                "trust level unlocks it and no decision can, because the "
                "Body checks the combination before it reads the decision. "
                "If any key reaches Windows, the run's most important "
                "assertion has failed."
            ),
            pre=lambda: DANGEROUS_COMBO,
            decide=lambda info: self.decide(
                ActionCategory.MOUSE_KEYBOARD, "+".join(info)
            ),
            act=lambda decision, info, answer: self.hands.hotkey(decision, info),
            expect=DangerousHotkeyRefused,
            check=lambda r: (
                "Ctrl+Alt+Del was not refused, so the combination may "
                "have reached Windows"
            ),
            pass_line=(
                "the blocklist fired before the actuator was touched, so no "
                "key was sent."
            ),
            abort_on_check_fail=True,
            shot="phase-c-after-refusal",
        ):
            return False
        if not self.run_step(
            "C",
            2,
            total,
            "verify-no-security-screen",
            about_to=(
                "Read the window that currently has focus and ask you to "
                "confirm the desktop looks unchanged."
            ),
            consequence=(
                "The focused window's title is printed and checked against "
                "'Windows Security'. If the security screen had appeared, the "
                "combination reached the operating system and the run stops "
                "here."
            ),
            decide=lambda info: self.decide(ActionCategory.READ_FILES, ""),
            act=lambda decision, info, answer: self._foreground_facts(decision),
            check=lambda r: None
            if "windows security" not in str(r.get("title", "")).lower()
            else (
                "the foreground window is the Windows security screen — the "
                "combination reached the operating system"
            ),
            describe=lambda r: f"foreground window: {r.get('title') or 'none'}",
            abort_on_check_fail=True,
            shot="phase-c-after-refusal",
        ):
            return False
        print()
        print("Phase C complete: the refusal is the assertion mocks cannot")
        print("make, because only a real keyboard proves nothing was sent.")
        return True

    def _foreground_facts(self, decision: PermissionDecision) -> Dict[str, Any]:
        """Return the focused window's facts as a plain mapping.

        Args:
            decision: The ``read_files`` decision for the read.

        Returns:
            The window record with its title coerced to a string, so the
            assertion can check it even when no window has focus.
        """
        window = self.hands.get_active_window(decision) or {}
        facts = dict(window)
        facts["title"] = str(facts.get("title", ""))
        return facts

    # ------------------------------------------------------------------
    # Phase D — watchdog trigger
    # ------------------------------------------------------------------

    def phase_d(self) -> bool:
        """Run Phase D: trip the watchdog with a real click burst.

        Returns:
            True when the run should continue.
        """
        total = 4
        if not self.run_step(
            "D",
            1,
            total,
            "choose-safe-click-spot",
            about_to=(
                "Compute the centre of the primary display and validate it "
                "against every attached monitor. No click happens in this step."
            ),
            consequence=(
                "The point is printed below. Look at the screen yourself: it "
                "must be empty desktop, not a window, an icon or a taskbar "
                "item — the next step clicks that exact point up to sixty "
                "times. If anything is there, do not type GO; move the window "
                "first."
            ),
            pre=self._safe_click_spot,
            act=lambda decision, info, answer: info,
            describe=lambda r: f"click spot ({r['x']}, {r['y']}) "
            f"on monitor {r['monitor']['index']}",
        ):
            return False
        if not self.run_step(
            "D",
            2,
            total,
            "rapid-click-burst",
            about_to=(
                f"Click the chosen point up to {BURST_CLICKS} times as fast as "
                f"the actuator allows, polling the watchdog every "
                f"{WATCHDOG_POLL_EVERY} clicks."
            ),
            consequence=(
                "This is a deliberate input burst. The watchdog's click "
                "signal (50 clicks in 10s; docs/BODY_HANDS.md, section 5) "
                "should trip partway through and engage the emergency stop "
                "for real: the cursor stops mid-burst and stays stopped. "
                "The click spot must be empty desktop — whatever is under "
                "it receives the clicks."
            ),
            decide=lambda info: self.decide(
                ActionCategory.MOUSE_KEYBOARD,
                coordinate_target(info["x"], info["y"]),
            ),
            act=self._click_burst,
            check=lambda r: None
            if (r.get("watchdog") or {}).get("tripped")
            else (
                "the watchdog did not trip; "
                f"report: {(r.get('watchdog') or {}).get('reason') or 'none'}"
            ),
            describe=self._describe_burst,
            shot="phase-d-after-burst",
        ):
            return False
        if not self.run_step(
            "D",
            3,
            total,
            "verify-stop-engaged",
            about_to=(
                "Attempt an ordinary cursor read with a freshly built decision."
            ),
            consequence=(
                "While the stop is engaged, every action is refused with a "
                "kill-switch refusal — the HTTP-409 equivalent. This proves "
                "the stop outranks the decision, which is the whole point "
                "of the switch."
            ),
            decide=lambda info: self.decide(ActionCategory.READ_FILES, ""),
            act=lambda decision, info, answer: self.hands.get_position(decision),
            expect=KillSwitchRefused,
            check=lambda r: None
            if self.kill_switch.is_killed()
            else "the kill switch is not engaged, so the watchdog did not stop ZUZZ",
            pass_line=(
                "an ordinary action was refused while the stop is engaged — "
                "the same refusal the API reports as HTTP 409."
            ),
        ):
            return False
        if not self.run_step(
            "D",
            4,
            total,
            "confirm-what-you-saw",
            about_to=(
                "Ask you what the last minute looked like: the cursor should "
                "have frozen mid-burst and the stop banner should be showing. "
                "Nothing touches the machine in this step."
            ),
            consequence=(
                "Your answer is recorded. If the cursor did not freeze, skip "
                "this step — the watchdog report from step 2 is in the log "
                "either way."
            ),
            act=lambda decision, info, answer: {"observed": True},
            describe=lambda r: "you confirmed what the burst looked like",
        ):
            return False
        return True

    def _safe_click_spot(self) -> Dict[str, Any]:
        """Return a validated point in the middle of the primary display.

        The centre is checked against every attached monitor rather than
        assumed valid, because a click burst into a rejected coordinate
        would be refused partway and confuse the watchdog's report.

        Returns:
            The resolved point, with the monitor it landed on.

        Raises:
            OffScreenRefused: The point is on no attached monitor.
        """
        x, y = self.centre()
        return self.hands.screens.check(x, y)

    def _click_burst(
        self, decision: PermissionDecision, point: Dict[str, Any], answer: str
    ) -> Dict[str, Any]:
        """Click one point rapidly until the watchdog trips.

        The burst is one step rather than sixty, because the watchdog's
        click signal is a rate: fifty clicks inside ten seconds. Pausing
        three seconds between clicks the way separate steps do would
        spread the burst past the window and prove nothing.

        Args:
            decision: The ``mouse_keyboard`` decision for the click point.
            point: The validated click spot.
            answer: The typed go-ahead, unused — the burst has no
                per-click input.

        Returns:
            How many clicks were sent, and the watchdog report that ended
            the burst.
        """
        x, y = point["x"], point["y"]
        clicks = 0
        report: Dict[str, Any] = {}
        for attempt in range(BURST_CLICKS):
            try:
                self.hands.click(decision, x, y, interval=BURST_INTERVAL)
            except KillSwitchRefused:
                break
            clicks += 1
            if (attempt + 1) % WATCHDOG_POLL_EVERY == 0:
                report = self.watchdog.poll()
                if report.get("tripped"):
                    break
        if not report.get("tripped"):
            report = self.watchdog.poll()
        return {"clicks": clicks, "watchdog": report}

    @staticmethod
    def _describe_burst(result: Dict[str, Any]) -> str:
        """Describe a finished burst for the console.

        Args:
            result: The burst's click count and watchdog report.

        Returns:
            One line naming how many clicks were sent and whether the
            watchdog tripped, with its reason.
        """
        report = result.get("watchdog") or {}
        state = "TRIPPED" if report.get("tripped") else "did not trip"
        return (
            f"{result.get('clicks')} clicks sent; watchdog {state}: "
            f"{report.get('reason') or 'no reason recorded'}"
        )

    # ------------------------------------------------------------------
    # Phase E — kill switch reset
    # ------------------------------------------------------------------

    def phase_e(self) -> bool:
        """Run Phase E: reset the stop with the word the policy demands.

        Returns:
            True when the run should continue.
        """
        total = 3
        if not self.run_step(
            "E",
            1,
            total,
            "read-reset-requirements",
            about_to=(
                "Ask the kill switch what a reset requires right now: the "
                "confirmation word, and how long you must wait."
            ),
            consequence=(
                "Nothing changes. The first reset after a stop waits 0 "
                "seconds; resets repeated within an hour wait "
                f"{COOLDOWN_LADDER[1]}s, then {COOLDOWN_LADDER[2]}s, then "
                f"{COOLDOWN_LADDER[3]}s — a ladder that slows a toggle loop "
                "without punishing one ordinary stop."
            ),
            act=lambda decision, info, answer: self._reset_requirements(),
            describe=self._describe_requirements,
        ):
            return False
        if not self.run_step(
            "E",
            2,
            total,
            "type-reset-word",
            about_to=(
                f"Disengage the emergency stop. The policy requires the word "
                f"'{RESET_WORD}' typed and an explicit confirmation; this "
                "prompt collects both at once."
            ),
            consequence=(
                "The stop flag is removed from both locations and ZUZZ — "
                "including the running assistant — is armed again. A wrong "
                "word or an unmet cooldown is refused and the stop stays "
                "engaged; that refusal is recorded, not fatal."
            ),
            confirm_word=RESET_WORD,
            ignore_case=True,
            act=lambda decision, info, answer: self._reset(answer),
            describe=lambda r: (
                "emergency stop disengaged "
                f"({r['resets_this_hour']} reset(s) in the last hour)"
            ),
        ):
            return False
        if not self.run_step(
            "E",
            3,
            total,
            "verify-armed",
            about_to=(
                "Read the cursor position with a fresh decision — the "
                "same harmless probe as step A1."
            ),
            consequence=(
                "If this succeeds, the switch is disengaged and every system "
                "is armed. If it is refused, the reset did not take and the "
                "machine is still stopped."
            ),
            decide=lambda info: self.decide(ActionCategory.READ_FILES, ""),
            act=lambda decision, info, answer: self.hands.get_position(decision),
            check=lambda r: None
            if not self.kill_switch.is_killed()
            else "the kill switch is still engaged after the reset",
            describe=lambda r: f"cursor at ({r[0]}, {r[1]}) — armed again",
            shot="phase-e-armed",
        ):
            return False
        return True

    def _reset_requirements(self) -> Dict[str, Any]:
        """Return what the switch currently demands of a reset.

        Returns:
            Whether the stop is engaged, how long a reset would wait, how
            many resets the last hour saw, and the switch's own wording.
        """
        status = self.kill_switch.status()
        return {
            "engaged": status["engaged"],
            "cooldown_remaining": status["cooldown_remaining"],
            "resets_this_hour": status["resets_this_hour"],
            "reset_instruction": status["reset_instruction"],
        }

    @staticmethod
    def _describe_requirements(result: Dict[str, Any]) -> str:
        """Describe the reset requirements for the console.

        Args:
            result: The switch status subset from the step.

        Returns:
            One line naming the cooldown and the reset count.
        """
        return (
            f"engaged={result['engaged']}, "
            f"cooldown={result['cooldown_remaining']}s, "
            f"resets this hour={result['resets_this_hour']}"
        )

    def _reset(self, typed_word: str) -> Dict[str, Any]:
        """Disengage the stop with the word the policy requires.

        Args:
            typed_word: The word the human typed at the prompt.

        Returns:
            The switch status after the reset.

        Raises:
            ResetRefused: The word was wrong, the cooldown has not
                elapsed, or no stop is engaged — in which case there
                is nothing to reset and the switch stays as it is.
        """
        if not self.kill_switch.is_killed():
            raise ResetRefused(
                "the stop is not engaged, so there is nothing to reset"
            )
        status = self.kill_switch.reset(typed_word=typed_word, confirmed=True)
        return {"engaged": status["engaged"], "resets_this_hour": status["resets_this_hour"]}

    # ------------------------------------------------------------------
    # Phase F — clean shutdown
    # ------------------------------------------------------------------

    def phase_f(self) -> bool:
        """Run Phase F: undo everything the run can undo, then report.

        Returns:
            True when the run should continue.
        """
        total = 5
        if not self.run_step(
            "F",
            1,
            total,
            "release-held-keys",
            about_to=(
                "Release every key the run is holding. This is one of the "
                "three decision-free operations — it undoes damage rather "
                "than causing it, so it asks no permission."
            ),
            consequence=(
                "Any modifier the run pressed is let go. The keyboard is "
                "left in the state you chose."
            ),
            act=lambda decision, info, answer: {
                "released": self.hands.release_held_keys()
            },
            describe=lambda r: f"released: {', '.join(r['released']) or 'none were held'}",
        ):
            return False
        if not self.run_step(
            "F",
            2,
            total,
            "clear-clipboard",
            about_to="Empty the system clipboard.",
            consequence=(
                "Whatever was on the clipboard before the run is gone — paste "
                "anything you need somewhere safe first. The run itself never "
                "wrote to the clipboard, so only your own copies are lost."
            ),
            decide=lambda info: self.decide(ActionCategory.WRITE_FILES, ""),
            act=lambda decision, info, answer: self.hands.clipboard_clear(decision),
            describe=lambda r: "clipboard emptied",
        ):
            return False
        if not self._phase_f_return_mouse(total):
            return False
        if not self.run_step(
            "F",
            4,
            total,
            "write-audit-entry",
            about_to=(
                "Append one entry summarising this run to the safety audit log."
            ),
            consequence=(
                "The audit log at storage/logs/audit_log.jsonl gains a "
                "kill_switch record whose summary says plainly that it is "
                "the live verification run — so a security review can find "
                "the run without knowing when it happened."
            ),
            act=lambda decision, info, answer: self._write_audit_entry(),
            describe=lambda r: f"audit entry written at ts {r['ts']}",
        ):
            return False
        if not self.run_step(
            "F",
            5,
            total,
            "print-summary",
            about_to=(
                "Print every action, its outcome and any refusal "
                "reason, plus the exact re-arming commands."
            ),
            consequence=(
                "The summary is printed as soon as this step is recorded; "
                "nothing else touches the machine after this step."
            ),
            act=lambda decision, info, answer: {"total": len(self.records) + 1},
            describe=lambda r: f"{r['total']} steps recorded, this summary included",
        ):
            return False
        return True

    def _phase_f_return_mouse(self, total: int) -> bool:
        """Return the cursor to the recorded origin, or say why not.

        Phase A's first step records the origin; when that step was
        skipped there is no origin to return to, and inventing one would
        move the cursor somewhere the user never had it.

        Args:
            total: The number of steps in Phase F, for the header.

        Returns:
            True when the run should continue.
        """
        if self.origin is None:
            self.record(
                "F",
                "return-mouse-to-origin",
                "skipped",
                None,
                detail={"reason": "step A1 was skipped, so no origin was recorded"},
                summary="skipped — no origin was recorded",
            )
            print()
            print("[Phase F] step 3/5: return-mouse-to-origin")
            print("SKIPPED — step A1 was skipped, so there is no recorded")
            print("origin to return the cursor to. The mouse stays where it is.")
            self.pause()
            return True
        origin_x, origin_y = self.origin
        return self.run_step(
            "F",
            3,
            total,
            "return-mouse-to-origin",
            about_to=(
                f"Move the real cursor back to the position Phase A recorded "
                f"({origin_x}, {origin_y})."
            ),
            consequence=(
                "The pointer ends the run exactly where it began, so the "
                "verification leaves no visible trace on the desktop."
            ),
            decide=lambda info: self.decide(
                ActionCategory.MOUSE_KEYBOARD, coordinate_target(origin_x, origin_y)
            ),
            act=lambda decision, info, answer: self.hands.move(
                decision, origin_x, origin_y
            ),
            describe=lambda r: f"cursor at ({r['x']}, {r['y']}) — back where Phase A found it",
            shot="phase-f-restored",
        )

    def _write_audit_entry(self) -> Dict[str, Any]:
        """Append this run's summary to the safety audit log.

        The run engaged and reset the emergency stop, so its summary
        belongs beside those events under the kill_switch type; the summary
        text says plainly that it is the verification record.

        Returns:
            The audit entry's timestamp, for the console.
        """
        counts = self.counts()
        assertions = self.assertion_counts()
        summary = (
            "live verification run completed: "
            f"{sum(counts.values())} steps "
            f"({counts['ok']} ok, {counts['refused']} refused, "
            f"{counts['skipped']} skipped, {counts['error']} error); "
            f"{assertions['checked']} assertions checked, "
            f"{assertions['failed']} failed; log={self.log_path.name}"
        )
        entry = self.audit.record(
            KILL_SWITCH,
            summary,
            category="hands",
            target="scripts/live_verify_hands.py",
            level="info",
            source="live_verify",
            outcomes=dict(counts),
        )
        return {"ts": entry["ts"]}

    # ------------------------------------------------------------------
    # Summary and re-arming
    # ------------------------------------------------------------------

    def counts(self) -> Dict[str, int]:
        """Count the recorded outcomes.

        Returns:
            A mapping with one key per outcome kind.
        """
        totals = {"ok": 0, "refused": 0, "skipped": 0, "error": 0}
        for record in self.records:
            outcome = str(record.get("outcome"))
            if outcome in totals:
                totals[outcome] += 1
        return totals

    def assertion_counts(self) -> Dict[str, int]:
        """Count the assertions the run checked.

        Returns:
            How many assertions were checked and how many of them failed.
        """
        return {
            "checked": self.assertions_checked,
            "failed": self.assertions_failed,
        }

    def _curl_body(self, payload: Dict[str, Any]) -> str:
        """Return a JSON body quoted for curl on a Windows command line.

        Args:
            payload: The request body.

        Returns:
            The JSON with its quotes backslash-escaped, so the whole body
            survives inside curl's double-quoted ``-d`` argument.
        """
        return json.dumps(payload).replace('"', '\\"')

    def reset_command(self) -> str:
        """Return the exact command that disengages the emergency stop.

        Returns:
            A copy-pasteable curl command carrying the confirmation word
            and the explicit confirmation the reset policy requires.
        """
        body = self._curl_body({"typed_word": RESET_WORD, "confirmed": True})
        return (
            f"curl -X POST {self.base_url()}/api/kill-switch/reset "
            f'-H "Content-Type: application/json" -d "{body}"'
        )

    def _trust_body(self, level: int) -> Dict[str, Any]:
        """Return the request body that restores one trust level.

        The body mirrors the escalating confirmation in
        ``config/trust_levels.json``: level 1 is instant, levels 2-4 need
        an acknowledged confirmation, and level 5 additionally needs the
        CONFIRM word and an acknowledged cooldown — the same evidence the
        web UI collects.

        Args:
            level: The trust level being restored.

        Returns:
            The request body for ``POST /api/safety/trust``.
        """
        requirement = self.trust.requirement_for(level)
        body: Dict[str, Any] = {"level": level}
        if requirement != "instant":
            body["confirmed"] = True
        if requirement == "type_confirm":
            body["typed_word"] = self.trust.confirmation_word()
            body["acknowledged_cooldown"] = True
        return body

    def trust_command(self, level: int) -> str:
        """Return the exact command that sets one trust level.

        Args:
            level: The trust level to set.

        Returns:
            A copy-pasteable curl command carrying the confirmation the
            level's policy demands.
        """
        body = self._curl_body(self._trust_body(level))
        return (
            f"curl -X POST {self.base_url()}/api/safety/trust "
            f'-H "Content-Type: application/json" -d "{body}"'
        )

    def rearm_commands(self) -> List[str]:
        """Return the commands that re-engage every safety guard.

        Returns:
            The labelled, copy-pasteable command list the summary prints,
            covering the kill switch, held keys, the clipboard and the
            trust level the run started from.
        """
        lines = [
            "1. Reset the kill switch (skip this if Phase E already did):",
            f"    {self.reset_command()}",
            "2. Release any key the run left held:",
            f"    curl -X POST {self.base_url()}/api/hands/release-keys",
            "3. Clear the clipboard:",
            f"    curl -X DELETE {self.base_url()}/api/hands/clipboard",
            f"4. Restore the trust level to its value before the "
            f"run (it was {self.trust_level}):",
            f"    {self.trust_command(self.trust_level)}",
        ]
        if self.notepad_open:
            lines.append(
                "5. Close the Notepad window Phase B left open — the run never "
                "closes it, so the text you verified is still in it."
            )
        return lines

    def print_summary(self) -> Dict[str, int]:
        """Print every step's outcome and the exact re-arming commands.

        Returns:
            The outcome counts, for the JSONL record of this step.
        """
        counts = self.counts()
        assertions = self.assertion_counts()
        print()
        print("=" * 64)
        print(f" SUMMARY — {len(self.records)} steps | log: {self.log_path}")
        print("=" * 64)
        for record in self.records:
            phase = record.get("phase")
            name = str(record.get("step"))
            outcome = str(record.get("outcome"))
            if outcome == "refused" and record.get("refusal_reason"):
                note = str(record["refusal_reason"])
                if (record.get("detail") or {}).get("expected"):
                    note = f"expected — {note}"
            else:
                note = str(record.get("summary") or "")
            print(f" {phase}  {name:<30} {outcome:<9} {note[:70]}")
        print("-" * 64)
        expected = sum(
            1
            for record in self.records
            if record["outcome"] == "refused"
            and (record.get("detail") or {}).get("expected")
        )
        print(
            f"outcomes: {counts['ok']} ok, {counts['refused']} refused "
            f"({expected} expected), {counts['skipped']} skipped, "
            f"{counts['error']} error"
        )
        print(
            f"assertions: {assertions['checked']} checked, "
            f"{assertions['failed']} failed"
        )
        print()
        for line in self.rearm_commands():
            print(line)
        print("=" * 64)
        return {"total": len(self.records), **counts}

    # ------------------------------------------------------------------
    # The run itself
    # ------------------------------------------------------------------

    def banner(self) -> None:
        """Print the warning that heads every run."""
        print("=" * 64)
        print(" ZUZZ LIVE VERIFICATION — Body/Hands (docs/BODY_HANDS.md, section 8)")
        print("=" * 64)
        print(" THIS MOVES THE REAL CURSOR AND TYPES REAL KEYSTROKES.")
        print(" Keep your hands off the mouse and keyboard until the run is done.")
        print(" Ctrl+C at any moment: engages the emergency stop, releases")
        print(" every held key, and prints how to recover.")
        print(" Every step asks before it acts. Type GO to run it; any other")
        print(" input skips it, and the skip is recorded like any other outcome.")
        print("=" * 64)

    def plan(self) -> None:
        """Print the run's shape once the startup guards have passed."""
        layout = self.hands.describe_layout()
        primary = layout["primary"]
        print(
            f" display: primary {primary['width']}x{primary['height']}, "
            f"{len(layout['monitors'])} monitor(s), scale {layout['scale']}"
        )
        print(f" log: {self.log_path}")
        print(f" screenshots: {self.shot_dir}")
        print("-" * 64)
        print(" The run has 6 phases and 22 steps, with a 3-second pause")
        print(" between steps. Phase D deliberately trips the watchdog: the")
        print(" emergency stop will engage for real, and Phase E resets it.")
        print(" Press Ctrl+C now if you are not ready.")

    def execute(self) -> None:
        """Run the six phases in order, stopping at the first abort."""
        for phase in (
            self.phase_a,
            self.phase_b,
            self.phase_c,
            self.phase_d,
            self.phase_e,
            self.phase_f,
        ):
            if not phase():
                return

    def emergency_stop(self, reason: str, last_action: str) -> None:
        """The Ctrl+C path: stop everything, then say how to recover.

        Machine state comes before messaging: the stop is triggered and the keys
        released before a single line is printed, so a second interrupt landing
        mid-handler still leaves the machine stopped and the keyboard clean.

        The two halves are guarded separately, on purpose. If triggering the stop
        itself fails, the handler must still tell the user the reset command —
        telling them "the stop engaged" when it may not have would be worse than
        useless, but leaving them without the recovery command is what strands
        someone staring at a ZUZZ that will not respond.

        Args:
            reason: What the user interrupted.
            last_action: The step that was in flight.
        """
        engaged = False
        try:
            self.kill_switch.trigger(
                source=SOURCE_UI, reason=reason, last_action=last_action
            )
            engaged = True
        except BaseException as exc:
            # A second interrupt landing inside this handler must not be able to
            # skip the messaging below.
            print(f"could not engage the emergency stop: {type(exc).__name__}: {exc}")
        try:
            self.hands.release_held_keys()
        except BaseException:
            pass
        try:
            print()
            if engaged:
                print("Ctrl+C — the emergency stop is ENGAGED. Every ZUZZ action is")
                print("refused, including the running assistant, until the stop is reset.")
            else:
                print("Ctrl+C — the run was interrupted before the stop could be confirmed.")
                print("Treat the machine as stopped and check the stop yourself:")
            print("Held keys have been released. The mouse is where it stopped.")
            print("Recover with:")
            print(f"    {self.reset_command()}")
            print("Then re-run the script, or finish the remaining phases by")
            print("hand (docs/BODY_HANDS.md, section 8.5).")
        except BaseException:
            pass


def main() -> int:
    """Run the live verification and report how it ended.

    Returns:
        0 when every assertion passed, 2 when the run finished with an
        assertion failing, 1 when it never completed, and 130 when the
        user interrupted it.
    """
    try:
        run = LiveVerify()
    except Exception as exc:
        print(f"cannot start: {type(exc).__name__}: {exc}")
        print("The Body layer could not be built against real")
        print("hardware. On a headless machine there is nothing to")
        print("verify; run this at a desk with a display.")
        return 1
    run.banner()
    if not run.check_server():
        return 1
    if not run.check_switch_disengaged():
        return 1
    if not run.check_trust_level():
        return 1
    run.plan()
    try:
        run.execute()
    except KeyboardInterrupt:
        run.emergency_stop(
            "live verification interrupted by Ctrl+C",
            last_action=run.current_step,
        )
        run.print_summary()
        return 130
    run.print_summary()
    if run.aborted:
        return 1
    if run.assertion_counts()["failed"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
