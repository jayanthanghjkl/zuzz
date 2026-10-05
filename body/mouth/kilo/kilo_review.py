"""Body/Mouth/Kilo — static checks and review.

Two kinds of review, deliberately different in what they can be wrong about:

* :func:`checks` is **AST-based and deterministic**. It parses the file and reports
  what is there — a bare ``except``, an ``eval``, a shadowed builtin, an import never
  used. It cannot hallucinate: the same source gives the same findings every time,
  which is what makes it worth running on every proposal without spending a model call.
* :meth:`Reviewer.review` adds a **model** pass, the only thing that can notice a logic
  error, a missing error path or an insecure design. Its findings are labelled
  ``model``, because a 120B model's confident wrong answer is indistinguishable from a
  correct one at the call site. Mixing the two without labels would let a deterministic
  finding and a guess be weighed equally, and the guess would win by being novel.

Neither writes. The Debugger proposes a fix; this module never applies one.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from body.mouth.kilo.exceptions import DecisionRefused, ModelUnavailable

__all__ = ["Finding", "Reviewer", "checks", "get_reviewer", "parse_model_findings"]

#: Builtins a project should not shadow. Shadowing ``id`` or ``input`` in a module
#: scope breaks every other module that imported it from there.
RISKY_SHADOWS = frozenset({
    "id", "input", "type", "list", "dict", "set", "filter", "map", "next", "open", "help",
})

#: Calls worth a second look even though they are not banned outright.
RISKY_CALLS = frozenset({"eval", "exec", "compile", "__import__", "input"})

#: Severities, most serious first.
ORDER = {"bug": 0, "security": 1, "quality": 2, "unused": 3, "info": 4}


@dataclass(frozen=True)
class Finding:
    """One thing worth telling the user about.

    Attributes:
        severity: ``bug``, ``security``, ``quality``, ``unused`` or ``info``.
        where: ``file:line``, so the Code page can point at it.
        message: What is wrong.
        fix: What to do about it.
        source: ``static`` for a deterministic finding, ``model`` for a guess.
    """

    severity: str
    where: str
    message: str
    fix: str = ""
    source: str = "static"

    def to_dict(self) -> Dict[str, str]:
        """Return the finding as plain data."""
        return {"severity": self.severity, "where": self.where, "message": self.message,
                "fix": self.fix, "source": self.source}


def _rank(finding: Finding) -> Tuple[int, str]:
    """Return a sort key placing the most serious findings first."""
    return (ORDER.get(finding.severity, 5), finding.where)


def checks(source: str, path: str = "code.py") -> List[Finding]:
    """Run the deterministic checks over one source file.

    Pure: takes text, returns findings, touches nothing. That is why it needs no
    decision — it is a function over a string, not an act.

    Args:
        source: The Python source.
        path: The name to report findings against.

    Returns:
        The findings, most serious first. A file that does not parse yields one
        ``bug`` finding saying so rather than an empty list — silence here would read
        as "clean", and an unparseable file is the opposite of clean.
    """
    try:
        tree = ast.parse(str(source or ""), filename=str(path))
    except SyntaxError as exc:
        return [Finding("bug", f"{path}:{exc.lineno or 0}",
                        f"this file does not parse: {exc.msg}",
                        "fix the syntax error before anything else is worth checking")]

    found: List[Finding] = []
    used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    used |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}

    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                name = (alias.asname or alias.name).split(".")[0]
                if name != "*" and name not in used:
                    found.append(Finding("unused", f"{path}:{line}",
                                         f"{name!r} is imported but never used",
                                         f"remove the import of {name!r}"))
        elif isinstance(node, ast.ExceptHandler) and node.type is None:
            found.append(Finding("quality", f"{path}:{line}",
                                 "bare except swallows everything, including KeyboardInterrupt",
                                 "catch the exception you expect"))
        elif isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if name in RISKY_CALLS:
                found.append(Finding("security", f"{path}:{line}",
                                     f"{name}(...) executes code at runtime",
                                     "use a literal, a mapping or an import"))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for argument in list(node.args.args) + list(node.args.kwonlyargs):
                if argument.arg in RISKY_SHADOWS:
                    found.append(Finding("quality", f"{path}:{line}",
                                         f"parameter {argument.arg!r} shadows a builtin",
                                         f"rename {argument.arg!r} to something specific"))
    found.sort(key=_rank)
    return found


def parse_model_findings(text: str) -> List[Finding]:
    """Parse the Reviewer agent's reply into findings.

    The format is ``SEVERITY|file:line|what is wrong|how to fix``, one per line,
    because a reviewer asked for prose returns prose and one asked for a table returns
    a table. ``NONE`` is accepted and means there is nothing.

    Args:
        text: The reviewer's reply.

    Returns:
        The findings, each labelled ``model``.
    """
    found: List[Finding] = []
    for raw in str(text or "").splitlines():
        line = raw.strip().strip("`")
        if not line or line.upper().startswith("NONE"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 3:
            continue
        severity = parts[0].lower() if parts[0].lower() in ORDER else "info"
        found.append(Finding(severity, parts[1], parts[2],
                             parts[3] if len(parts) > 3 else "", source="model"))
    found.sort(key=_rank)
    return found


class Reviewer:
    """Combines the deterministic checks with the model pass."""

    def __init__(self, config: Optional[Any] = None, logger: Optional[Any] = None) -> None:
        """Wire the reviewer to the model router.

        Args:
            config: The :class:`~body.mouth.kilo.kilo_config.KiloConfig` for the model
                pass. ``None`` means static checks only, which :meth:`review` says in
                its response rather than pretending a model looked at the file.
            logger: Activity logger.
        """
        self.config = config
        self.logger = logger

    @property
    def can_review_with_a_model(self) -> bool:
        """Return True when a model pass is available."""
        return self.config is not None

    async def review(self, decision: Any, path: str, source: str) -> Dict[str, Any]:
        """Review one file.

        Args:
            decision: The caller's already-verified decision. Required for the model
                pass; the static pass needs nothing, because it acts on nothing.
            path: The file's name, used in every finding.
            source: The file's text.

        Returns:
            The findings, counts by severity, and whether a model looked at it.

        Raises:
            DecisionRefused: No decision, when a model pass was asked for.
            ModelUnavailable: Neither the cloud provider nor the local fallback
                answered.
        """
        findings = checks(source, path)
        model_used = False
        note = "static checks only; no model router is wired"
        if self.config is not None:
            if decision is None:
                raise DecisionRefused("a model review needs a decision the caller already holds")
            result = await self.config.infer(
                decision, "reviewer",
                f"Review this file for bugs, security problems and dead code.\n\n"
                f"--- {path} ---\n{str(source)[:40_000]}",
            )
            model_used = True
            note = result.reason or f"reviewed by {result.model}"
            findings = findings + parse_model_findings(result.text)

        counts: Dict[str, int] = {}
        for finding in findings:
            counts[finding.severity] = counts.get(finding.severity, 0) + 1
        blocking = counts.get("bug", 0) + counts.get("security", 0)
        self._log("kilo", "review", path=path, findings=len(findings), blocking=blocking)
        return {
            "path": path, "findings": [f.to_dict() for f in findings], "counts": counts,
            "total": len(findings), "blocking": blocking,
            "verdict": "changes wanted" if blocking else "no blocking findings",
            "model_reviewed": model_used, "model_note": note,
        }

    def capabilities(self) -> Dict[str, Any]:
        """Return what this reviewer can do. Not gated; a gauge.

        Shown on the Code page so a user knows whether "no findings" means a clean
        file or a file nobody read.
        """
        return {
            "model_reviewed": self.can_review_with_a_model,
            "checks": ["unused imports", "bare except", "eval/exec/compile/__import__",
                       "shadowed builtins", "syntax errors"],
            "severities": list(ORDER), "deterministic": True,
        }

    def _log(self, action: str, **fields: Any) -> None:
        """Record a review in the activity log, when wired."""
        if self.logger is None:
            return
        try:
            self.logger.log("kilo", action, **fields)
        except Exception:
            pass


_REVIEWER: Optional[Reviewer] = None


def get_reviewer(config: Optional[Any] = None, logger: Optional[Any] = None) -> Reviewer:
    """Return the process-wide reviewer, building it on first use.

    Args:
        config: The model router, or None for a static-only reviewer.
        logger: Activity logger, used the first time only.
    """
    global _REVIEWER
    if _REVIEWER is None:
        _REVIEWER = Reviewer(config=config, logger=logger)
    return _REVIEWER
