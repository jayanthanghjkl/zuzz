"""Body/Tools — the registry.

The single place a tool name is bound to an implementation. The model may only
name something that appears here, and the schema catalogue in
:mod:`brain.cerebrum.tools_schema` may only describe something that appears
here. :func:`catalogue_mismatches` exists so that disagreement is a test
failure rather than a tool the model is told about and cannot reach.

A name in the schema with no implementation is worse than a missing tool: the
model has been promised a hand, will answer "done", and nothing will happen.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from brain.cerebrum.tool_catalogue import TOOL_NAMES
from brain.cerebrum.tools_schema import schema_for

from body.tools.app_tools import APP_TOOLS
from body.tools.browser_tools import BROWSER_TOOLS
from body.tools.clipboard_tools import CLIPBOARD_TOOLS
from body.tools.file_tools import FILE_TOOLS
from body.tools.keyboard_tools import KEYBOARD_TOOLS
from body.tools.mouse_tools import MOUSE_TOOLS
from body.tools.system_tools import SYSTEM_TOOLS
from body.tools.tool_spec import ToolSpec
from body.tools.vision_tools import VISION_TOOLS

__all__ = ["ToolRegistry", "catalogue_mismatches", "get_registry"]


class ToolRegistry:
    """Every tool the model may call, looked up by name.

    Attributes:
        specs: The tools, in catalogue order.
    """

    def __init__(self, specs: Tuple[ToolSpec, ...]) -> None:
        """Build the registry.

        Args:
            specs: The tools to offer.

        Raises:
            ValueError: If a name appears twice. Two implementations of one name
                means the second silently never runs, which is the failure this
                registry exists to make impossible.
        """
        self.specs = tuple(specs)
        self._by_name: Dict[str, ToolSpec] = {}
        for spec in self.specs:
            if spec.name in self._by_name:
                raise ValueError(f"duplicate tool name: {spec.name}")
            self._by_name[spec.name] = spec

    def get(self, name: str) -> Optional[ToolSpec]:
        """Return one tool, or None when the model named something else.

        Args:
            name: The tool name from the model.

        Returns:
            The spec, or None. Callers must refuse on None rather than falling
            back to a default — there is no safe default action.
        """
        return self._by_name.get(str(name))

    def names(self) -> Tuple[str, ...]:
        """Return every tool name, in catalogue order."""
        return tuple(spec.name for spec in self.specs)

    def describe(self) -> List[Dict[str, str]]:
        """Return every tool as plain data, for a status endpoint or a log."""
        return [spec.describe() for spec in self.specs]


def catalogue_mismatches() -> List[str]:
    """Return every way the schemas and the registry disagree.

    Returns:
        One line per mismatch, empty when the two catalogues agree. Checked by
        the test suite on both sides: a schema with no implementation promises
        the model a capability that does not exist, and an implementation with
        no schema is a capability no model can reach.
    """
    problems: List[str] = []
    for name in TOOL_NAMES:
        if get_registry().get(name) is None:
            problems.append(f"{name}: described to the model with no implementation")
    for name in get_registry().names():
        if schema_for(name) is None:
            problems.append(f"{name}: implemented but not described to the model")
    return problems


_REGISTRY: Optional[ToolRegistry] = None


def get_registry() -> ToolRegistry:
    """Return the process-wide tool registry, building it on first use.

    Built lazily rather than at import so that importing this module does not
    drag in PyAutoGUI and the Windows API bindings — the face routes already
    import them, and the tests that never execute a tool should not pay for it.
    """
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = ToolRegistry(
            MOUSE_TOOLS
            + KEYBOARD_TOOLS
            + CLIPBOARD_TOOLS
            + APP_TOOLS
            + FILE_TOOLS
            + VISION_TOOLS
            + BROWSER_TOOLS
            + SYSTEM_TOOLS
        )
    return _REGISTRY
