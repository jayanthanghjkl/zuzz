"""Basal Ganglia — the habit orchestrator.

Turns observed behaviour into routines, stores them, and works out whether one
may run.

**The all-or-nothing precheck is the point of this module.** A routine is a
sequence of acts on the user's own desktop, and the steps are decided
individually by the Prefrontal Cortex. Executing them in order and stopping at
the first refusal is the obvious implementation and the wrong one: a five-step
routine refused at step three has already opened two applications and focused a
window, leaving the desktop half-configured with no single action the user took to
explain it. So every step is resolved and decided **before** any step runs, and
one refusal abandons the whole thing. See :meth:`BasalGanglia.plan`.

**Storing a routine stores no permission.** The decision is taken at run time, at
the trust level in force then. A routine built when ZUZZ was at level 5 and run
at level 1 does only what level 1 permits, because :meth:`plan` re-resolves every
step and asks the gate again. This is why ``Routine`` carries no decision, no
approval and no trust level: there is nowhere for one to be hidden.

**The gate is injected, not built.** Passed in from ``create_app`` so it is the
same gate every other action in the process is judged by. A gate constructed here
would be a second reading of the matrix, and two readings means two answers.

Execution itself is not done here. :meth:`execute_routine` takes an ``executor``
callable and, given none, returns a full plan without acting — so the *Body*
layer runs the steps through the ordinary ``ToolSpec.resolve`` /
``ToolSpec.execute`` pipeline, and this module only ever decides what would be
permitted. That ordering is what keeps a routine from becoming a second path into
the Body with its own idea of what needs checking.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Callable, Dict, List, Optional, Sequence

from brain.basal_ganglia.context_triggers import Trigger, describe, due, parse
from brain.basal_ganglia.exceptions import (
    RoutineDisabled,
    RoutineNameInvalid,
    RoutineNotPermitted,
    RoutineRefused,
    StepRefused,
    UnknownRoutine,
)
from brain.basal_ganglia.pattern_detector import (
    DEFAULT_MIN_REPETITIONS,
    Pattern,
    detect_patterns,
)
from brain.basal_ganglia.routine_builder import (
    MAX_STEPS,
    Routine,
    RoutineStep,
    build_from_pattern,
    validate_name,
)
from brain.basal_ganglia.routine_registry import RoutineRegistry
from brain.prefrontal_cortex.trust_matrix import ASK, AUTO, BLOCKED

__all__ = ["BasalGanglia", "PlannedStep", "RoutinePlan", "RoutineResult"]

#: The trust category a routine's own creation, editing and running is judged as.
#: Read from the matrix rather than hardcoded, so renaming the row is a config
#: change rather than a code change.
ROUTINE_CATEGORY = "routine_management"


class PlannedStep:
    """One resolved step of a routine, ready to run.

    Attributes:
        index: Position in the routine.
        step: The stored step.
        target: The resolved :class:`~body.tools.tool_spec.ToolTarget`.
        decision: The gate's verdict for this step at this trust level.
    """

    __slots__ = ("index", "step", "target", "decision")

    def __init__(self, index: int, step: RoutineStep, target: Any, decision: Any) -> None:
        """Record one planned step.

        Args:
            index: Position in the routine.
            step: The stored step.
            target: Its resolved target.
            decision: The gate's verdict.
        """
        self.index = int(index)
        self.step = step
        self.target = target
        self.decision = decision

    @property
    def verdict(self) -> str:
        """Return ``auto``, ``ask`` or ``blocked`` for this step.

        The three are kept apart because they mean different things to a caller,
        and collapsing "ask" into "refused" would be wrong. ``close_app`` is an
        ``ask`` at *every* trust level by design — closing an application can lose
        unsaved work — so a routine containing one is not permanently forbidden,
        it is permanently *confirmable*. Treating that as a refusal would make
        every routine that closes something permanently unrunnable.
        """
        return str(getattr(self.decision, "decision", "ask"))

    @property
    def allowed(self) -> bool:
        """Return True when the gate permits this step without asking."""
        return self.verdict == AUTO

    @property
    def needs_confirmation(self) -> bool:
        """Return True when a human must approve this step."""
        return self.verdict == ASK

    @property
    def blocked(self) -> bool:
        """Return True when no trust level would ever allow this step."""
        return self.verdict == BLOCKED

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable form."""
        return {
            "index": self.index,
            "step": self.index + 1,
            "tool": self.step.tool,
            "params": dict(self.step.params),
            "target": getattr(self.target, "target", ""),
            "category": getattr(self.target, "category", ""),
            "verdict": self.verdict,
            "allowed": self.allowed,
            "needs_confirmation": self.needs_confirmation,
            "blocked": self.blocked,
            "reason": getattr(self.decision, "reason", ""),
        }


class RoutinePlan:
    """Every step of a routine, resolved and decided, before anything runs.

    Attributes:
        routine: The routine the plan is for.
        trust_level: The level the gate was asked at.
        steps: The planned steps, in order.
        refused: Steps the gate would never allow. Empty is common and is not a
            problem.
        pending: Steps a human must approve. A routine can be run once these are
            confirmed, and refused outright once they are not.
    """

    __slots__ = ("routine", "trust_level", "steps", "refused", "pending")

    def __init__(
        self,
        routine: Routine,
        trust_level: int,
        steps: Sequence[PlannedStep],
        refused: Sequence[Dict[str, Any]],
        pending: Sequence[Dict[str, Any]] = (),
    ) -> None:
        """Record a plan.

        Args:
            routine: The routine planned.
            trust_level: The level the gate was asked at.
            steps: Every step, decided.
            refused: Detail of steps no trust level allows.
            pending: Detail of steps needing a human's approval.
        """
        self.routine = routine
        self.trust_level = int(trust_level)
        self.steps = list(steps)
        self.refused = list(refused)
        self.pending = list(pending)

    @property
    def permitted(self) -> bool:
        """Return True when no step is forbidden.

        Note this does **not** mean every step will run without asking — a plan
        with pending confirmations is permitted but not yet runnable. Use
        :attr:`runnable` for that question.
        """
        return not self.refused

    @property
    def runnable(self) -> bool:
        """Return True when every step either may run or has been confirmed.

        A caller supplies ``confirmed`` to :meth:`BasalGanglia.execute_routine` to
        say which pending steps a human approved.
        """
        return self.permitted and not self.pending

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable form."""
        return {
            "routine": self.routine.name,
            "trust_level": self.trust_level,
            "permitted": self.permitted,
            "runnable": self.runnable,
            "steps": [step.to_dict() for step in self.steps],
            "refused": list(self.refused),
            "pending": list(self.pending),
        }


class RoutineResult:
    """What happened when a routine ran.

    Attributes:
        plan: The plan it was run against.
        outcomes: One entry per step, for a run that happened.
        executed: Whether anything actually ran.
        reason: Why not, when it did not.
    """

    __slots__ = ("plan", "outcomes", "executed", "reason")

    def __init__(
        self,
        plan: RoutinePlan,
        outcomes: Optional[Sequence[Dict[str, Any]]] = None,
        executed: bool = False,
        reason: str = "",
    ) -> None:
        """Record a run.

        Args:
            plan: The plan the run was made against.
            outcomes: Per-step results.
            executed: Whether the steps ran.
            reason: Why they did not, when they did not.
        """
        self.plan = plan
        self.outcomes = list(outcomes or [])
        self.executed = bool(executed)
        self.reason = str(reason)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable form."""
        return {
            "routine": self.plan.routine.name,
            "executed": self.executed,
            "reason": self.reason,
            "trust_level": self.plan.trust_level,
            "permitted": self.plan.permitted,
            "runnable": self.plan.runnable,
            "steps": len(self.plan.steps),
            "outcomes": list(self.outcomes),
            "refused": list(self.plan.refused),
        }


class BasalGanglia:
    """Habits, routines, and the gate decision on running one.

    Attributes:
        registry: Where routines are stored.
        gate: The Prefrontal Cortex gate, injected so it is the same one every
            other action is judged by.
        trust: Reads the current trust level. Optional so a caller can pass one
            explicitly; without it, level 1 is assumed, which is the level that
            cannot surprise anyone.
        tool_context: Builds the :class:`~body.tools.tool_spec.ToolContext` that
            step targets are resolved against. Optional, and the running app injects
            one — see :meth:`_resolve_context` for what happens without it.
    """

    def __init__(
        self,
        registry: Optional[RoutineRegistry] = None,
        gate: Any = None,
        trust: Any = None,
        tool_context: Optional[Callable[[], Any]] = None,
    ) -> None:
        """Wire up the habit layer.

        Args:
            registry: Where routines are stored. Defaults to
                ``config/routines.json``.
            gate: The permission gate. Built from the matrix when omitted, which is
                what a standalone test or a script wants; the running app always
                injects the shared one.
            trust: Something with a ``current()`` returning an int, i.e. a
                :class:`~brain.prefrontal_cortex.trust_manager.TrustManager`.
            tool_context: Callable returning the collaborators a step's tool may
                act through.
        """
        self.registry = registry if registry is not None else RoutineRegistry()
        self._gate = gate
        self._trust = trust
        self._tool_context = tool_context

    @property
    def gate(self) -> Any:
        """Return the gate, building a matrix-reading one on first use."""
        if self._gate is None:
            from brain.prefrontal_cortex.permission_gate import PermissionGate

            self._gate = PermissionGate()
        return self._gate

    def current_level(self) -> int:
        """Return the trust level in force right now.

        Returns:
            The level, or 1 when no trust manager was injected. Assuming the
            lowest level is what makes a standalone caller conservative rather
            than permissive.
        """
        if self._trust is None:
            return 1
        try:
            return int(self._trust.current())
        except Exception:
            return 1

    def _resolve_context(self) -> Any:
        """Return the collaborators a step's tool may act through.

        Falls back to an **empty** :class:`ToolContext` rather than ``None`` when
        nothing was injected. A step whose tool only needs a trust category — most
        of them — resolves perfectly well against an empty context, so a routine
        written and inspected outside the running app still plans. What it cannot do
        is run, and a tool that needs the Hypothalamus refuses with its own
        "not attached" message, which names the missing piece.

        Passing ``None`` instead is what this replaces, and it produced
        ``AttributeError: 'NoneType' object has no attribute 'hypothalamus'`` from
        inside the tool — an unhandled 500 for a request that should have said
        which step could not be resolved and why.

        Returns:
            The context, or ``None`` when the tool_spec module itself cannot be
            imported, which would only happen in a broken install.
        """
        if self._tool_context is not None:
            try:
                return self._tool_context()
            except Exception:
                return None
        try:
            from body.tools.tool_spec import ToolContext

            return ToolContext(hands=None, files=None, directories=None)
        except Exception:  # pragma: no cover - broken install
            return None

    # -----------------------------------------------------------------
    # Detection
    # -----------------------------------------------------------------

    def detect_patterns(
        self,
        activity_log: Optional[Sequence[Dict[str, Any]]] = None,
        min_repetitions: int = DEFAULT_MIN_REPETITIONS,
    ) -> List[Pattern]:
        """Find the habits in the activity log.

        Args:
            activity_log: Activity-log records, oldest first. Read from the
                shared activity logger when omitted.
            min_repetitions: How many times a sequence must appear to count.

        Returns:
            Patterns, strongest first.
        """
        records = activity_log
        if records is None:
            from brain.brainstem.activity_logger import get_logger

            records = get_logger().tail(2000)
        return detect_patterns(records, min_repetitions=min_repetitions)

    # -----------------------------------------------------------------
    # Storage
    # -----------------------------------------------------------------

    def list_routines(self) -> List[Routine]:
        """Return every stored routine, in file order."""
        return self.registry.load()

    def get_routine(self, name: str) -> Routine:
        """Return one routine by name.

        Args:
            name: The routine name.

        Returns:
            The routine.

        Raises:
            UnknownRoutine: When no routine by that name is stored.
        """
        routine = self.registry.get(name)
        if routine is None:
            raise UnknownRoutine(f"there is no routine called {str(name)!r}")
        return routine

    def create_routine(
        self,
        pattern: Any,
        name: Optional[str] = None,
        trigger: Any = None,
        description: str = "",
    ) -> Routine:
        """Store a routine, built from a detected pattern or given directly.

        Args:
            pattern: A :class:`~brain.basal_ganglia.pattern_detector.Pattern`, or
                plain data describing a routine when the user is writing one
                rather than having it detected.
            name: The routine's name.
            trigger: When it fires. Manual when omitted.
            description: One line describing it.

        Returns:
            The routine as stored, always disarmed.

        Raises:
            RoutineNameInvalid: When the name is unusable or already taken.
            StepRefused: When a step names something ZUZZ cannot call.
            RoutineRefused: When the pattern could not be turned into a routine.
        """
        if isinstance(pattern, Pattern):
            routine = build_from_pattern(pattern, name=name, trigger=trigger)
            if description:
                routine = Routine(**{**routine.__dict__, "description": str(description)})
        else:
            from brain.basal_ganglia.routine_registry import routine_from_dict

            payload = dict(pattern) if isinstance(pattern, dict) else {}
            payload["name"] = name or payload.get("name")
            if description:
                payload["description"] = str(description)
            if trigger is not None:
                payload["trigger"] = trigger
            routine = routine_from_dict(payload)

        if not routine.steps:
            raise StepRefused("a routine with no steps would do nothing")
        if len(routine.steps) > MAX_STEPS:
            raise StepRefused(
                f"a routine may hold at most {MAX_STEPS} steps; this has "
                f"{len(routine.steps)}"
            )
        for step in routine.steps:
            if not step.tool:
                raise StepRefused("every step needs a tool name")
        # Forced here rather than trusted from the payload. A routine that
        # arrives armed can act on a trigger before the person who wrote it has
        # read it, and this method is the only way a routine gets stored — so
        # the invariant belongs here, not in each of the callers that happen to
        # remember to set it. Arming is `update_routine({"enabled": True})`,
        # which is a separate, later, deliberate act.
        routine = dataclasses.replace(routine, enabled=False)
        self._check_steps_callable(routine)
        if self.registry.get(routine.name) is not None:
            raise RoutineNameInvalid(
                f"a routine called {routine.name!r} already exists; "
                "update it instead of creating a second one"
            )
        return self.registry.upsert(routine)

    def _check_steps_callable(self, routine: Routine) -> None:
        """Refuse a routine naming something ZUZZ cannot call, at save time.

        :meth:`plan` already refuses it, so this is not the only line of
        defence. It is here because of *when* it fires. Without it, a routine
        naming a tool that does not exist is accepted, stored, armed by the
        user once they have read it, and then fails partway through doing
        something to the desktop — the first steps applied, the bad one
        stopping the rest, with no single user action to explain it. The
        failure is knowable when the routine is written, so it is raised when
        the routine is written.

        The check is deliberately the cheap half only — the tool exists and its
        schema accepts the parameters. Permission is *not* consulted, because a
        step that is not permitted at the current trust level is a normal state
        for a routine and is exactly what :meth:`plan` exists to report.

        Args:
            routine: The routine about to be stored.

        Raises:
            StepRefused: When a step names a tool ZUZZ does not have, or passes
                parameters its schema will not accept.
        """
        from body.tools.registry import get_registry
        from brain.cerebrum.tools_validate import ToolParamError, validate

        registry = get_registry()
        for index, step in enumerate(routine.steps):
            if registry.get(step.tool) is None:
                raise StepRefused(
                    f"step {index + 1} of {routine.name!r} calls {step.tool!r}, "
                    "which is not a tool ZUZZ has"
                )
            try:
                validate(step.tool, step.params)
            except ToolParamError as exc:
                raise StepRefused(
                    f"step {index + 1} of {routine.name!r} has parameters "
                    f"{step.tool!r} will not accept: {exc}"
                ) from exc

    def update_routine(self, name: str, changes: Dict[str, Any]) -> Routine:
        """Change a stored routine.

        Args:
            name: The routine to change.
            changes: Any of ``description``, ``trigger``, ``steps``, ``enabled``.

        Returns:
            The routine as stored.

        Raises:
            UnknownRoutine: When there is no such routine.
            RoutineNameInvalid: When a rename is requested to an unusable name.
            StepRefused: When new steps name something ZUZZ cannot call.
            TriggerRefused: When the new trigger is not a kind ZUZZ knows.
        """
        existing = self.get_routine(name)
        unknown = sorted(set(changes or {}) - {
            "description",
            "trigger",
            "steps",
            "enabled",
            "name",
        })
        if unknown:
            raise RoutineNameInvalid(
                f"a routine cannot be changed by: {', '.join(unknown)}. "
                "Changeable: description, trigger, steps, enabled, name"
            )

        updated: Dict[str, Any] = {**existing.__dict__}
        if "description" in changes:
            updated["description"] = str(changes["description"] or "")
        if "trigger" in changes:
            raw = changes["trigger"]
            updated["trigger"] = raw if isinstance(raw, Trigger) else parse(raw)
        if "enabled" in changes:
            updated["enabled"] = bool(changes["enabled"])
        if "steps" in changes:
            updated["steps"] = tuple(
                RoutineStep(
                    tool=str(step.get("tool", "")).strip(),
                    params=dict(step.get("params", {}) or {}),
                    category=str(step.get("category", "")),
                    target=str(step.get("target", "")),
                    why=str(step.get("why", "")),
                )
                for step in changes["steps"]
                if isinstance(step, dict)
            )
        new_name = str(changes.get("name", "") or "").strip().lower()
        if new_name and new_name != existing.name:
            validate_name(new_name)
            if self.registry.get(new_name) is not None:
                raise RoutineNameInvalid(f"a routine called {new_name!r} already exists")
            updated["name"] = new_name

        candidate = Routine(**updated)
        if not candidate.steps:
            raise StepRefused("a routine with no steps would do nothing")
        if "steps" in changes:
            # The same check creation does. Editing a routine's steps is exactly
            # as capable of naming something ZUZZ cannot call as writing one, and
            # the edited routine may already be armed — so the bad step would not
            # wait for the next arm, it would be waiting in an armed routine.
            self._check_steps_callable(candidate)
        self.registry.delete(existing.name)
        return self.registry.upsert(candidate)

    def delete_routine(self, name: str) -> bool:
        """Remove a routine.

        Args:
            name: The routine to remove.

        Returns:
            True when it was there and is now gone. False when there was nothing
            to remove — deleting twice is not an error.
        """
        return self.registry.delete(name)

    def seed(self) -> int:
        """Add the shipped routines without touching any the user has changed.

        Returns:
            How many were added.
        """
        return self.registry.seed()

    # -----------------------------------------------------------------
    # Triggers
    # -----------------------------------------------------------------

    def due_routines(self, context: Optional[Dict[str, Any]] = None) -> List[Routine]:
        """Return the enabled routines whose trigger fires now.

        Args:
            context: What is known right now — ``at``/``now``, ``apps``,
                ``message``.

        Returns:
            The routines that are due, in storage order.
        """
        return [
            routine
            for routine in due(self.list_routines(), context)
            if routine.enabled
        ]

    # -----------------------------------------------------------------
    # Planning and running
    # -----------------------------------------------------------------

    def plan(self, name: str, trust_level: Optional[int] = None) -> RoutinePlan:
        """Resolve and decide every step of a routine, before anything runs.

        Args:
            name: The routine to plan.
            trust_level: The level to ask the gate at. Read from the trust
                manager when omitted.

        Returns:
            The plan, listing every step with its verdict and any that were
            refused.

        Raises:
            UnknownRoutine: When there is no such routine.
            RoutineDisabled: When the routine is switched off.
            StepRefused: When a step names a tool ZUZZ does not have, or one whose
                parameters its schema will not accept. Raised *before* any
                decision is taken, because a step that cannot run makes the rest
                of the plan meaningless.
        """
        routine = self.get_routine(name)
        if not routine.enabled:
            raise RoutineDisabled(
                f"the routine {routine.name!r} is switched off; enable it first",
                routine,
            )

        from body.tools.registry import get_registry
        from body.tools.tool_spec import ToolRefused
        from brain.cerebrum.tools_validate import ToolParamError, validate
        from brain.prefrontal_cortex.action import Action

        registry = get_registry()
        level = self.current_level() if trust_level is None else int(trust_level)
        context = self._resolve_context()

        planned: List[PlannedStep] = []
        refused: List[Dict[str, Any]] = []
        pending: List[Dict[str, Any]] = []

        for index, step in enumerate(routine.steps):
            spec = registry.get(step.tool)
            if spec is None:
                raise StepRefused(
                    f"step {index + 1} of {routine.name!r} calls {step.tool!r}, "
                    "which is not a tool ZUZZ has"
                )
            # Validated against the tool's own schema before it is decided, so a
            # step that could never run is reported as a bad step rather than as a
            # permission problem the user can do nothing about.
            try:
                params = validate(step.tool, step.params)
            except ToolParamError as exc:
                raise StepRefused(
                    f"step {index + 1} of {routine.name!r} has parameters "
                    f"{step.tool!r} will not accept: {exc}"
                ) from exc
            try:
                target = spec.resolve(params, context)
            except ToolRefused as exc:
                raise StepRefused(
                    f"step {index + 1} of {routine.name!r} cannot be resolved: {exc}"
                ) from exc
            except (AttributeError, TypeError) as exc:
                # A tool that pokes at a collaborator the context does not carry.
                # Reported as a named bad step rather than allowed to escape as a
                # 500, because the user can act on "step 3 cannot be resolved" and
                # cannot act on an AttributeError from inside the registry.
                raise StepRefused(
                    f"step {index + 1} of {routine.name!r} calls {step.tool!r}, "
                    f"which needs something this machine has not attached: {exc}"
                ) from exc
            decision = self.gate.decide(
                Action(category=target.category, target=target.target, detail={"routine": routine.name}),
                level,
            )
            entry = PlannedStep(index, step, target, decision)
            planned.append(entry)
            detail = {
                "index": index,
                "step": index + 1,
                "tool": step.tool,
                "target": getattr(target, "target", ""),
                "category": getattr(target, "category", ""),
                "reason": getattr(decision, "reason", "not permitted"),
            }
            if entry.blocked:
                refused.append(detail)
            elif entry.needs_confirmation:
                pending.append(detail)

        return RoutinePlan(routine, level, planned, refused, pending)

    def execute_routine(
        self,
        name: str,
        context: Any = None,
        executor: Optional[Callable[..., Any]] = None,
        trust_level: Optional[int] = None,
        confirmed: Optional[Sequence[int]] = None,
    ) -> RoutineResult:
        """Plan a routine and, when given an executor, run it.

        Args:
            name: The routine to run.
            context: The :class:`~body.tools.tool_spec.ToolContext` the steps act
                through. Required when an executor is given; the plan itself does
                not use it.
            executor: ``(spec, decision, params, context) -> result``, supplied by
                the Body layer. When omitted, this returns the full plan without
                acting — which is what a caller wanting to show the user what a
                routine would do gets.
            trust_level: The level to ask the gate at. Read from the trust
                manager when omitted.
            confirmed: Step numbers a human approved, 1-based and as the user
                would count them. Needed only when the plan has pending steps,
                which is the normal state for a routine that closes an
                application: ``close_app`` asks at every trust level, so such a
                routine can never run unattended and asking for confirmation is
                the correct behaviour rather than a defect.

        Returns:
            What happened. With no executor, ``executed`` is False and ``reason``
            explains that the routine was only planned.

        Raises:
            UnknownRoutine: When there is no such routine.
            RoutineDisabled: When the routine is switched off.
            RoutineNotPermitted: When a step is forbidden outright, or when a step
                is awaiting confirmation the caller did not give. Raised rather
                than returned, because the refusal is the answer and a caller who
                ignored a ``permitted: false`` would run the routine anyway.
            StepRefused: When a step cannot be resolved.
        """
        plan = self.plan(name, trust_level=trust_level)
        approved = {int(number) for number in (confirmed or [])}

        if not plan.permitted:
            summary = "; ".join(
                f"step {entry['step']} ({entry['tool']}) {entry['reason']}"
                for entry in plan.refused
            )
            raise RoutineNotPermitted(
                f"the routine {plan.routine.name!r} was not run — {summary}. "
                "Nothing was changed: every step is checked before any of them runs.",
                plan.trust_level,
                plan.refused,
            )

        unconfirmed = [entry for entry in plan.pending if entry["step"] not in approved]
        if unconfirmed:
            summary = "; ".join(
                f"step {entry['step']} ({entry['tool']}) {entry['reason']}"
                for entry in unconfirmed
            )
            raise RoutineNotPermitted(
                f"the routine {plan.routine.name!r} is waiting on confirmation — "
                f"{summary}. Nothing was changed.",
                plan.trust_level,
                unconfirmed,
            )

        if executor is None:
            return RoutineResult(
                plan, executed=False, reason="planned only; no executor was supplied"
            )

        outcomes: List[Dict[str, Any]] = []
        for entry in plan.steps:
            from body.tools.registry import get_registry

            spec = get_registry().get(entry.step.tool)
            params = dict(entry.step.params)
            try:
                result = executor(spec, entry.decision, params, context)
            except Exception as exc:  # noqa: BLE001 - reported per step, not raised
                outcomes.append(
                    {
                        "index": entry.index,
                        "tool": entry.step.tool,
                        "status": "error",
                        "detail": type(exc).__name__,
                        "message": str(exc),
                    }
                )
                break
            outcomes.append(
                {
                    "index": entry.index,
                    "tool": entry.step.tool,
                    "status": "executed",
                    "detail": result,
                }
            )

        self._record_run(plan.routine)
        return RoutineResult(plan, outcomes=outcomes, executed=True)

    def _record_run(self, routine: Routine) -> None:
        """Note that a routine ran, so the routines list can show it."""
        import time

        self.registry.upsert(
            Routine(
                **{
                    **routine.__dict__,
                    "times_run": routine.times_run + 1,
                    "last_run": time.time(),
                    "updated": time.time(),
                }
            )
        )

    # -----------------------------------------------------------------
    # Reporting
    # -----------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        """Return a description of the habit layer, for the dashboard."""
        routines = self.list_routines()
        return {
            "path": str(self.registry.path),
            "count": len(routines),
            "enabled": sum(1 for routine in routines if routine.enabled),
            "names": [routine.name for routine in routines],
            "trust_level": self.current_level(),
            "category": ROUTINE_CATEGORY,
            "by_source": _tally(routines),
        }

    def describe_routine(self, name: str) -> Dict[str, Any]:
        """Return one routine plus what running it would involve.

        Args:
            name: The routine to describe.

        Returns:
            The routine and, when it can be planned, the plan — so the Habits page
            can show what will happen before the user commits to it.

        Raises:
            UnknownRoutine: When there is no such routine.
            RoutineDisabled: When the routine is switched off.
            StepRefused: When a step cannot be resolved.
        """
        routine = self.get_routine(name)
        payload = routine.to_dict()
        try:
            payload["plan"] = self.plan(name).to_dict()
        except RoutineRefused as exc:
            payload["plan"] = {"error": str(exc), "permitted": False}
        return payload


def _tally(routines: Sequence[Routine]) -> Dict[str, int]:
    """Return a tally of routines by where they came from."""
    counts: Dict[str, int] = {}
    for routine in routines:
        counts[routine.source] = counts.get(routine.source, 0) + 1
    return counts


def describe_trigger(trigger: Any) -> str:
    """Return a trigger's one-line description. Re-exported for convenience."""
    return describe(trigger)