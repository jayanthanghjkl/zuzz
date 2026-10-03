"""Body/Fingers — refusals.

The Body layer never decides anything; it executes what a
:class:`~brain.prefrontal_cortex.permission_decision.PermissionDecision`
already allowed. When it refuses, it raises one of these, and the API
layer maps the type onto an HTTP status. Refusing is the Body's only
safety contribution, so every refusal says *why* in one line a user can
read.

The hierarchy matters for the API mapping: anything that lands on
:class:`ProtectedPathRefused` is a 403, because it means the target is
a place ZUZZ must never touch at any trust level.
"""

from __future__ import annotations


class BodyRefused(Exception):
    """Base class for every Body-layer refusal."""


class DecisionRefused(BodyRefused):
    """The operation reached the Body without permission to run.

    Covers a missing decision, a decision from another operation, and a
    decision that is not ``allowed`` — including one issued before the
    kill switch was engaged.
    """


class DecisionReplayRefused(DecisionRefused):
    """A decision was presented for a different operation or target."""


class DryRunRefused(BodyRefused):
    """A dry-run decision reached the Body, which must not perform I/O."""


class KillSwitchRefused(BodyRefused):
    """The kill switch was engaged after the decision was issued."""


class ProtectedPathRefused(BodyRefused):
    """The target is a location ZUZZ must never read, write or delete."""


class SecretFileRefused(ProtectedPathRefused):
    """The target is a credential file, blocked at every trust level."""


class ZuzzCoreRefused(ProtectedPathRefused):
    """The target is ZUZZ's own code, logs or safety state."""


class PathTraversalRefused(ProtectedPathRefused):
    """A relative path resolves outside the project directory."""


class FileTooLarge(BodyRefused):
    """The file is larger than the operation's size limit."""


class BinaryFileRefused(BodyRefused):
    """The file is binary and cannot be handled as text."""


class FileLockedError(BodyRefused):
    """Another process holds the file and the wait timed out."""


class TrashError(BodyRefused):
    """The trash could not accept or restore a file."""


class RestoreConflict(BodyRefused):
    """Restoring would overwrite a file that already exists."""
