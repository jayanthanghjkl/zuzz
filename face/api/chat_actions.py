"""Face API — the agent action loop.

The orchestrator between a model's reply and the machine. One tool call goes
through seven steps, in this order, and the order is the safety property:

1. **Parse.** :func:`~brain.cerebrum.action_parser.parse_actions` turns the raw
   reply into prose and :class:`~brain.cerebrum.action_parser.ToolCall` objects.
   Blocks that will not parse become ``errors`` — never silence.
2. **Resolve the target.** ``ToolSpec.resolve`` runs *before* the gate, so the
   gate judges ``chrome.exe`` rather than ``chrome``, and
   ``C:\\Users\\me\\notes.txt`` rather than ``notes.txt``. An alias cannot be
   re-pointed at something else between the decision and the launch, because the
   string that was decided is the string that will be acted on.
3. **Amygdala assess.** :class:`~brain.amygdala.threat_detector.ThreatDetector`
   scores the resolved action. A ``forbidden`` assessment vetoes; a
   ``dangerous`` one forces a confirmation. Nothing it says can turn a block
   into an allow.
4. **PermissionGate decide.** At the current trust level, with the threat
   attached. ``blocked`` / ``ask`` / ``auto``.
5. **Confirmation handling.** An ``ask`` parks the call and hands back a
   ``confirmation_id``. Nothing executes.
6. **Body execute.** ``ToolSpec.execute`` is called with an allowed decision and
   with nothing else — and the Body re-verifies that decision against the same
   category and target before it touches anything. Fifteen of the thirty-five
   tools are coroutines — everything the browser and the Occipital Lobe does —
   so this step awaits, which is why the runner's methods are ``async`` and the
   routes are too. A Playwright launch takes seconds; running it inline on the
   event loop would stall every other request on the server while one browser
   opened.
7. **Return the result.** As ``executed`` / ``blocked`` / ``pending`` / ``errors``
   / ``warnings`` / ``summary``.

**Where each outcome is written.** Every action, whatever its outcome, goes to
the activity logger — that is the complete record of what ZUZZ did. The
*security-relevant* outcomes also go to the audit log: a block, a confirmation
asked for, a confirmation granted, one denied, and a refusal that happened
before any decision existed. A successfully executed action deliberately does
not: ``core.audit.AuditLog`` refuses any event type that is not a safety event,
and ``routes_hands`` states the reasoning in the same place — the audit log
records what was refused and what was confirmed, and putting routine successes
in it would bury both.
"""

from __future__ import annotations

import inspect
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional

from brain.amygdala.threat_detector import ThreatDetector
from brain.cerebrum.action_parser import (
    MAX_ACTIONS_PER_REPLY,
    ParsedReply,
    ToolCall,
    parse_actions,
)
from brain.prefrontal_cortex.action import Action, ActionCategory
from brain.prefrontal_cortex.permission_decision import PermissionDecision
from brain.prefrontal_cortex.permission_gate import AUTO, PermissionGate
from core.audit import (
    BLOCKED,
    CONFIRMATION_DENIED,
    CONFIRMATION_GRANTED,
    CONFIRMATION_REQUESTED,
    PRECHECK_REFUSAL,
)

from body.fingers.exceptions import BodyRefused
from body.hands.exceptions import HandsRefused
from body.legs.exceptions import LegsRefused
from body.tools import ToolContext, ToolRefused, ToolSpec, get_registry
from brain.occipital_lobe.exceptions import VisionRefused

__all__ = [
    "ActionOutcome",
    "ChatActionRunner",
    "MAX_PENDING_CONFIRMATIONS",
    "PendingActionConfirmations",
]

#: How many parked chat confirmations are kept. Same reasoning as
#: :data:`face.api.file_confirmations.MAX_PENDING`: a confirmation is a prompt,
#: not a contract, and the in-memory registry is deliberately lost on restart so a
#: stale approval can never survive it.
MAX_PENDING_CONFIRMATIONS = 10


@dataclass(frozen=True)
class ActionOutcome:
    """What happened to one action the model asked for.

    Attributes:
        tool: The tool name.
        status: ``executed``, ``blocked``, ``pending`` or ``error``.
        target: The resolved target the gate judged, when resolution succeeded.
        category: The trust category that was decided.
        explanation: The model's own reason for asking, shown beside the badge.
        reason: The gate's or the Body's explanation, in plain language.
        result: What the tool produced, when it ran.
        confirmation_id: The id the user must quote to approve a ``pending``.
        warnings: Non-fatal notes worth showing — an unapproved application, a
            clipboard that may hold a secret.
        errors: Problems that stopped this one action, such as a tool name that
            does not exist.
    """

    tool: str
    status: str
    target: str = ""
    category: str = ""
    explanation: str = ""
    reason: str = ""
    result: Any = None
    confirmation_id: str = ""
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Return the outcome as plain data for a response or a log line."""
        payload: Dict[str, Any] = {
            "tool": self.tool,
            "status": self.status,
            "explanation": self.explanation,
        }
        if self.target:
            payload["target"] = self.target
        if self.category:
            payload["category"] = self.category
        if self.reason:
            payload["reason"] = self.reason
        if self.confirmation_id:
            payload["confirmation_id"] = self.confirmation_id
        if self.warnings:
            payload["warnings"] = list(self.warnings)
        if self.errors:
            payload["errors"] = list(self.errors)
        if self.status == "executed":
            payload["result"] = self.result
        return payload


class PendingActionConfirmations:
    """The last few chat actions awaiting a user's answer.

    Deliberately the same shape as
    :class:`face.api.file_confirmations.PendingConfirmations` — create, then
    ``pop`` — because ``pop`` is what makes an answer single-use. A ``get`` would
    leave the entry in the registry, so the same id could be posted twice and the
    action would run twice.
    """

    def __init__(self, limit: int = MAX_PENDING_CONFIRMATIONS) -> None:
        """Create the registry.

        Args:
            limit: How many parked confirmations to keep.
        """
        self._limit = limit
        self._entries: Dict[str, Dict[str, Any]] = {}
        self._order: List[str] = []
        self._lock = threading.Lock()
        self._counter = 0

    def create(
        self,
        call: ToolCall,
        target: Any,
        decision: PermissionDecision,
        threat: Any,
        warnings: List[str],
    ) -> str:
        """Park one action awaiting confirmation.

        Args:
            call: The model's call, already schema-validated.
            target: The :class:`~body.tools.tool_spec.ToolTarget` that was judged.
            decision: The gate's ``ask`` verdict.
            threat: The Amygdala's assessment, shown in the prompt.
            warnings: Non-fatal notes about this action.

        Returns:
            The ``confirmation_id`` the user is shown.
        """
        with self._lock:
            self._counter += 1
            confirmation_id = f"chat-conf-{int(time.time())}-{self._counter}"
            self._entries[confirmation_id] = {
                "call": call,
                "target": target,
                "decision": decision,
                "threat": threat,
                "warnings": list(warnings),
                "created": time.time(),
            }
            self._order.append(confirmation_id)
            while len(self._order) > self._limit:
                self._entries.pop(self._order.pop(0), None)
        return confirmation_id

    def pop(self, confirmation_id: str) -> Optional[Dict[str, Any]]:
        """Take back a parked confirmation, removing it in the same step.

        Args:
            confirmation_id: The id the user is answering.

        Returns:
            The parked entry, or None when unknown or already used.
        """
        with self._lock:
            entry = self._entries.pop(confirmation_id, None)
            if confirmation_id in self._order:
                self._order.remove(confirmation_id)
        return entry

    def __len__(self) -> int:
        """Return how many confirmations are parked."""
        return len(self._order)


class ChatActionRunner:
    """Runs the actions a model asked for, one at a time, through the gate.

    Attributes:
        state: The FastAPI ``app.state``, holding every collaborator.
    """

    def __init__(self, state: Any) -> None:
        """Bind the runner to an application's collaborators.

        Args:
            state: ``app.state``. Held rather than the collaborators
                individually, so a test can swap one of them and the runner
                follows.
        """
        self.state = state
        self.registry = get_registry()
        self.amygdala = ThreatDetector()
        #: Deliberately its own gate, not ``app.state.permission_gate``. The
        #: Hypothalamus shares that one with model management, and reaching it
        #: from a chat action would couple "may ZUZZ click this" to "may the
        #: resource layer evict a model". Both read the same matrix and the same
        #: kill switch, so the answers agree — but they are two questions.
        self.gate = PermissionGate(kill_switch=self.state.kill_switch)

    @property
    def confirmations(self) -> PendingActionConfirmations:
        """Return this application's parked confirmations, creating them on demand.

        Created lazily and stored on ``app.state`` rather than built in
        :func:`face.api.server.create_app`, so each application gets its own
        registry — two apps in one test process must not share a parked prompt —
        without the composition root having to know this module exists.
        """
        registry = getattr(self.state, "chat_confirmations", None)
        if registry is None:
            registry = PendingActionConfirmations()
            self.state.chat_confirmations = registry
        return registry

    # -----------------------------------------------------------------
    # Collaborators
    # -----------------------------------------------------------------

    def tool_context(self) -> ToolContext:
        """Return the collaborators a tool may act through.

        Returns:
            A fresh context. Built per action rather than cached, so an action
            confirmed several minutes later resolves against the collaborators
            as they are now, not as they were when the popup opened.
        """
        return ToolContext(
            hands=self.state.hands,
            files=self.state.file_manager,
            directories=self.state.directory_manager,
            hypothalamus=self.state.hypothalamus,
            # The vision, browser and system-awareness tools reach the Occipital
            # Lobe, the Legs layer and the Parietal Lobe through here. Without them
            # those families are registered but unreachable: the model is told about
            # a capability, calls it, and is refused with "not attached" — which is
            # how a capable agent starts claiming it saw a screen it never captured.
occipital_lobe=getattr(self.state, "occipital_lobe", None),
            browser=getattr(self.state, "legs", None),
            # The three Phase 6 regions, reached the same way. Without them the
            # memory, routine and personality tools are registered but
            # unreachable: the model is told ZUZZ can remember, calls it, and is
            # refused with "not attached" — which is how an agent starts claiming
            # it remembers things it never stored. Deliberately read with getattr
            # and a None default so an app built without them still serves.
            parietal_lobe=getattr(self.state, "parietal_lobe", None),
            hippocampus=getattr(self.state, "hippocampus", None),
            basal_ganglia=getattr(self.state, "basal_ganglia", None),
            cerebellum=getattr(self.state, "cerebellum", None),
            # The coding engine, reached the same way. Without it the ten kilo_*
            # tools are registered but unreachable: the model is told ZUZZ can
            # generate code, calls it, and is refused with "not attached" — which is
            # how an agent starts claiming it wrote a file it never wrote.
            kilo=getattr(self.state, "kilo", None),
        )

    # -----------------------------------------------------------------
    # Logging
    # -----------------------------------------------------------------

    def _activity(self, action: str, **fields: Any) -> None:
        """Record one action outcome in the activity log."""
        logger = getattr(self.state, "logger", None)
        if logger is not None:
            logger.log("face", action, **fields)

    def _audit(self, event: str, summary: str, **fields: Any) -> None:
        """Record a safety event in the audit log.

        A failure to write must not turn a decision into a crash: the decision is
        already made and the caller still needs an answer. The failure is written
        to the activity log instead, so a full disk is visible rather than silent.
        """
        audit = getattr(self.state, "audit", None)
        if audit is None:
            return
        try:
            audit.record(event, summary, **fields)
        except Exception as exc:  # pragma: no cover - defensive
            self._activity("audit_write_failed", event=event, error=str(exc))

    # -----------------------------------------------------------------
    # The pipeline
    # -----------------------------------------------------------------

    async def run_reply(self, text: str) -> Dict[str, Any]:
        """Parse a model reply and run every action it asked for.

        Args:
            text: The raw reply, action fences and all.

        Returns:
            The ``{executed, blocked, pending, errors, warnings, summary}``
            envelope. ``prose`` is included so the caller can return what the
            user should read rather than re-parsing.
        """
        parsed: ParsedReply = parse_actions(text)
        warnings: List[str] = []
        errors: List[Dict[str, Any]] = []

        # A block that could not be used is an **error**, not a warning. It was a
        # requested action that did not happen, and filing it under ``warnings``
        # beside "this application is not whitelisted" would make a silently
        # dropped tool call look like advice.
        for entry in parsed.dropped:
            errors.append(
                ActionOutcome(
                    tool=entry["tool"] or "(unnamed action)",
                    status="error",
                    errors=[entry["reason"]],
                ).to_dict()
            )

        executed: List[Dict[str, Any]] = []
        blocked: List[Dict[str, Any]] = []
        pending: List[Dict[str, Any]] = []

        if parsed.calls and len(parsed.calls) > MAX_ACTIONS_PER_REPLY:
            warnings.append(
                f"only the first {MAX_ACTIONS_PER_REPLY} actions in a reply are "
                "considered"
            )

        for call in parsed.calls:
            outcome = await self.run_call(call)
            for warning in outcome.warnings:
                if warning not in warnings:
                    warnings.append(warning)
            if outcome.status == "executed":
                executed.append(outcome.to_dict())
            elif outcome.status == "blocked":
                blocked.append(outcome.to_dict())
            elif outcome.status == "pending":
                pending.append(outcome.to_dict())
            else:
                errors.append(outcome.to_dict())

        return {
            "prose": parsed.prose,
            "executed": executed,
            "blocked": blocked,
            "pending": pending,
            "errors": errors,
            "warnings": warnings,
            "summary": self.summarise(executed, blocked, pending, errors),
            # True when the reply *tried* to act, not only when an action
            # survived. A reply whose every block was malformed produced nothing
            # to run and an error the user must see, so the UI still needs it.
            "has_actions": parsed.has_actions or bool(parsed.dropped),
        }

    async def run_call(self, call: ToolCall) -> ActionOutcome:
        """Run one call through resolve, assess, decide, confirm, execute.

        Args:
            call: The model's schema-validated request.

        Returns:
            The outcome. Never raises for anything the model can get wrong: a
            refusal is an outcome the user is shown, not an error that hides the
            other actions in the reply.
        """
        spec = self.registry.get(call.tool)
        if spec is None:
            # Checked twice — the parser refuses unknown names — but the registry
            # is the authority, and a name in the schema with no implementation is
            # a tool the model was promised and cannot reach.
            message = f"{call.tool}: no such tool. ZUZZ has {len(self.registry)}."
            self._activity("chat_action_error", tool=call.tool, reason=message)
            return ActionOutcome(
                tool=call.tool, status="error", explanation=call.explanation, errors=[message]
            )

        context = self.tool_context()
        try:
            target = spec.resolve(call.params, context)
        except ToolRefused as exc:
            # Refused while resolving, so no decision existed to make. Recorded as
            # a precheck refusal rather than a gate verdict, which is what it is:
            # a never-launch command or a path that climbs out of the project.
            self._audit(PRECHECK_REFUSAL, str(exc), tool=call.tool)
            self._activity("chat_action_precheck_refused", tool=call.tool, reason=str(exc))
            return ActionOutcome(
                tool=call.tool,
                status="blocked",
                explanation=call.explanation,
                reason=str(exc),
                errors=[str(exc)],
            )

        warnings: List[str] = []
        note = target.preview.get("warning")
        if note:
            warnings.append(str(note))

        action = Action(category=ActionCategory(target.category), target=target.target)
        level = self.state.trust.current()
        threat = self.amygdala.assess(action)
        decision = self.gate.decide(action, level, threat)

        self._activity(
            "chat_action_decided",
            tool=call.tool,
            category=target.category,
            target=target.target,
            decision=decision.decision,
            trust_level=level,
            threat_level=getattr(threat, "level", ""),
            threat_score=getattr(threat, "score", None),
        )

        if decision.blocked:
            self._audit(
                BLOCKED,
                decision.reason,
                category=target.category,
                target=target.target,
                level=decision.decision,
                rule_id=decision.rule_id or "",
                trust_level=decision.trust_level,
                tool=call.tool,
            )
            return ActionOutcome(
                tool=call.tool,
                status="blocked",
                target=target.target,
                category=target.category,
                explanation=call.explanation,
                reason=decision.reason,
                warnings=warnings,
            )

        if decision.requires_confirmation:
            confirmation_id = self.confirmations.create(
                call, target, decision, threat, warnings
            )
            self._audit(
                CONFIRMATION_REQUESTED,
                decision.reason,
                category=target.category,
                target=target.target,
                level=decision.decision,
                trust_level=decision.trust_level,
                tool=call.tool,
            )
            return ActionOutcome(
                tool=call.tool,
                status="pending",
                target=target.target,
                category=target.category,
                explanation=call.explanation,
                reason=decision.reason,
                confirmation_id=confirmation_id,
                warnings=warnings,
            )

        return await self._execute(spec, call, decision, target, warnings)

    async def confirm(
        self, confirmation_id: str, approval: bool
    ) -> Dict[str, Any]:
        """Answer a parked confirmation and act on it.

        The entry is **popped first**, so answering is single-use and the pop and
        the removal are one atomic step. The action is then **re-decided from
        scratch** — a fresh resolve, a fresh Amygdala pass and a fresh gate call
        at the current trust level — rather than the parked decision being
        upgraded blindly. A stop that landed while the popup was open still
        blocks, and a path that pointed somewhere harmless a minute ago is
        re-classified rather than trusted.

        Args:
            confirmation_id: The id from the ``pending`` entry.
            approval: Whether the user approved it.

        Returns:
            An envelope in the same shape :meth:`run_reply` returns, with
            ``confirmed`` naming the action and ``status`` saying what happened.

        Raises:
            LookupError: The id is unknown, expired or already used. The message
                is what the API returns as a 400.
        """
        entry = self.confirmations.pop(confirmation_id)
        if entry is None:
            raise LookupError("unknown or expired confirmation_id")

        call: ToolCall = entry["call"]
        parked_target = entry["target"]
        parked: PermissionDecision = entry["decision"]
        parked_threat = entry["threat"]
        warnings: List[str] = list(entry["warnings"])

        if not approval:
            self._audit(
                CONFIRMATION_DENIED,
                "the user declined this action",
                category=parked_target.category,
                target=parked_target.target,
                trust_level=parked.trust_level,
                tool=call.tool,
            )
            self._activity("chat_action_declined", tool=call.tool, target=parked_target.target)
            return self._envelope(
                prose="",
                outcomes=[
                    ActionOutcome(
                        tool=call.tool,
                        status="declined",
                        target=parked_target.target,
                        category=parked_target.category,
                        explanation=call.explanation,
                        reason="the user declined this action",
                        warnings=warnings,
                    )
                ],
            )

        spec = self.registry.get(call.tool)
        if spec is None:
            message = f"{call.tool}: no such tool"
            self._activity("chat_action_error", tool=call.tool, reason=message)
            return self._envelope(
                prose="",
                outcomes=[ActionOutcome(tool=call.tool, status="error", errors=[message])],
            )

        # A fresh resolve, because the world may have moved since the popup opened.
        context = self.tool_context()
        try:
            target = spec.resolve(call.params, context)
        except ToolRefused as exc:
            self._audit(PRECHECK_REFUSAL, str(exc), tool=call.tool)
            self._activity("chat_action_precheck_refused", tool=call.tool, reason=str(exc))
            return self._envelope(
                prose="",
                outcomes=[
                    ActionOutcome(
                        tool=call.tool,
                        status="blocked",
                        explanation=call.explanation,
                        reason=str(exc),
                        errors=[str(exc)],
                    )
                ],
            )

        action = Action(category=ActionCategory(target.category), target=target.target)
        level = self.state.trust.current()
        threat = self.amygdala.assess(action)
        fresh = self.gate.decide(action, level, threat)

        if fresh.blocked:
            self._audit(
                BLOCKED,
                fresh.reason,
                category=target.category,
                target=target.target,
                trust_level=fresh.trust_level,
                tool=call.tool,
            )
            self._activity(
                "chat_confirmation_reblocked",
                tool=call.tool,
                target=target.target,
                reason=fresh.reason,
            )
            return self._envelope(
                prose="",
                outcomes=[
                    ActionOutcome(
                        tool=call.tool,
                        status="blocked",
                        target=target.target,
                        category=target.category,
                        explanation=call.explanation,
                        reason=(
                            f"no longer permitted: {fresh.reason} "
                            f"(scored {getattr(parked_threat, 'level', '?')} when "
                            "you were asked)"
                        ),
                        warnings=warnings,
                    )
                ],
            )

        self._audit(
            CONFIRMATION_GRANTED,
            "the user approved this action",
            category=target.category,
            target=target.target,
            level=fresh.decision,
            trust_level=fresh.trust_level,
            tool=call.tool,
        )

        # The user approved this specific action, so the "ask" becomes an allow.
        # Only an "ask" reaches here: a block was refused by the branch above.
        # The threat assessment is deliberately dropped from the replacement —
        # carrying the old one forward would re-apply a "dangerous" flag to a
        # target that has just been re-scored on its own merits.
        granted = replace(
            fresh,
            allowed=True,
            requires_confirmation=False,
            decision=AUTO,
        )
        outcome = await self._execute(spec, call, granted, target, warnings)
        return self._envelope(prose="", outcomes=[outcome])

    # -----------------------------------------------------------------
    # Execution
    # -----------------------------------------------------------------

    async def _execute(
        self,
        spec: ToolSpec,
        call: ToolCall,
        decision: PermissionDecision,
        target: Any,
        warnings: List[str],
    ) -> ActionOutcome:
        """Call the Body with an allowed decision and report what came back.

        Args:
            spec: The resolved tool.
            call: The model's request.
            decision: The decision, always allowed by the time it arrives.
            target: The resolved target that was judged.
            warnings: Non-fatal notes collected during resolution.

        Returns:
            An ``executed`` outcome, or a ``blocked`` one when the Body itself
            refused. A Body refusal is a real safety event and is recorded as a
            block, because the decision said yes and the machine said no.
        """
        context = self.tool_context()
        try:
            # Awaited unconditionally rather than only when the tool looks
            # async. A coroutine that is merely *called* returns an un-awaited
            # object, which serialises as a repr instead of a result — so the
            # tool would be reported as executed while doing nothing at all.
            # That is precisely the hallucination this pipeline exists to
            # prevent, and it is why the await is here rather than in the tools.
            result = spec.execute(decision, call.params, context)
            if inspect.isawaitable(result):
                result = await result
        except (
            HandsRefused,
            BodyRefused,
            ToolRefused,
            VisionRefused,
            LegsRefused,
            ValueError,
            OSError,
        ) as exc:
            # ``OSError`` is in this list on purpose. A write to a path whose
            # parent does not exist, or a read of a file that was deleted between
            # the gate and the call, is a fact about the world that the user can
            # act on — it is not a defect, and reporting it as "internal error,
            # see the activity log" hides the only useful part of the answer.
            #
            # ``VisionRefused`` and ``LegsRefused`` are here for the same reason,
            # and it is the anti-hallucination requirement made concrete. "Moondream
            # is not running" and "no browser session is open" are answers the user
            # can act on; a generic internal-error badge is indistinguishable from
            # ZUZZ failing silently, which is the outcome the whole pipeline exists
            # to rule out. A refusal must be *said*, in its own words.
            self._audit(
                BLOCKED,
                str(exc),
                category=target.category,
                target=target.target,
                trust_level=decision.trust_level,
                tool=call.tool,
            )
            self._activity("chat_action_refused", tool=call.tool, reason=str(exc))
            return ActionOutcome(
                tool=call.tool,
                status="blocked",
                target=target.target,
                category=target.category,
                explanation=call.explanation,
                reason=str(exc),
                warnings=warnings,
                errors=[str(exc)],
            )
        except Exception as exc:
            # Anything else is a defect, not a refusal. The message is not
            # returned to the user — it may carry a path or a parameter value —
            # but it is written to the activity log so it can be found.
            self._activity(
                "chat_action_error", tool=call.tool, error=str(exc), error_type=type(exc).__name__
            )
            return ActionOutcome(
                tool=call.tool,
                status="error",
                target=target.target,
                category=target.category,
                explanation=call.explanation,
                reason="the action failed with an internal error",
                warnings=warnings,
                errors=[f"{call.tool}: internal error, see the activity log"],
            )

        self._activity(
            "chat_action_executed", tool=call.tool, category=target.category, target=target.target
        )
        return ActionOutcome(
            tool=call.tool,
            status="executed",
            target=target.target,
            category=target.category,
            explanation=call.explanation,
            result=result,
            warnings=warnings,
        )

    # -----------------------------------------------------------------
    # Envelopes
    # -----------------------------------------------------------------

    def _envelope(self, prose: str, outcomes: List[ActionOutcome]) -> Dict[str, Any]:
        """Sort outcomes into the standard envelope.

        Args:
            prose: The user-facing reply, when there is one.
            outcomes: Every outcome to file.

        Returns:
            The response shape, ready to return.
        """
        buckets: Dict[str, List[Dict[str, Any]]] = {
            "executed": [],
            "blocked": [],
            "pending": [],
            "errors": [],
        }
        warnings: List[str] = []
        for outcome in outcomes:
            if outcome.status == "executed":
                buckets["executed"].append(outcome.to_dict())
            elif outcome.status in ("blocked", "declined"):
                buckets["blocked"].append(outcome.to_dict())
            elif outcome.status == "pending":
                buckets["pending"].append(outcome.to_dict())
            else:
                buckets["errors"].append(outcome.to_dict())
            for warning in outcome.warnings:
                if warning not in warnings:
                    warnings.append(warning)
        envelope: Dict[str, Any] = dict(buckets)
        envelope["warnings"] = warnings
        envelope["summary"] = self.summarise(
            buckets["executed"], buckets["blocked"], buckets["pending"], buckets["errors"]
        )
        envelope["prose"] = prose
        envelope["has_actions"] = bool(outcomes)
        return envelope

    @staticmethod
    def summarise(
        executed: List[Dict[str, Any]],
        blocked: List[Dict[str, Any]],
        pending: List[Dict[str, Any]],
        errors: List[Dict[str, Any]],
    ) -> str:
        """Return one plain sentence describing the whole batch.

        Args:
            executed: Outcomes that ran.
            blocked: Outcomes the gate or the Body refused.
            pending: Outcomes waiting on the user.
            errors: Outcomes that could not be attempted.

        Returns:
            A sentence, or an empty string when nothing happened — which is the
            ordinary case for a conversational reply, and is left empty rather
            than filled with "no actions", so the UI can show nothing at all.
        """
        parts: List[str] = []
        if executed:
            parts.append(f"{len(executed)} action(s) ran")
        if pending:
            parts.append(f"{len(pending)} waiting for your approval")
        if blocked:
            parts.append(f"{len(blocked)} refused")
        if errors:
            parts.append(f"{len(errors)} could not be attempted")
        if not parts:
            return ""
        return ", ".join(parts) + "."