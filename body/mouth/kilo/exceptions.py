"""Body/Mouth/Kilo — refusals.

One hierarchy, so :mod:`face.api.routes_code` maps a refusal onto an HTTP status
by asking one question instead of enumerating classes in ten modules.

The tree encodes the distinction that actually matters here:

* **The gate's verdict and the Kilo layer's own verdict are different refusals.**
  :class:`DecisionRefused` means the Prefrontal Cortex said no, and a
  :class:`KiloRefused` subclass means the Body refused on its own reading
  *regardless* of what the gate said. A caller that cannot tell them apart will
  eventually write "retry after confirming", which can never help with the
  second kind.

* **A hard refusal has no confirmation path.** :class:`WorkspaceRefused` and
  :class:`GitRefused` are not questions, so nothing answers them.
  :class:`WorkspaceConfirmationRequired` is the single exception, and it is named
  for what it is: a request to be asked, carrying the workspace that is unknown.

The ordering is the API's status mapping: 403 for the gate and the layer's own
refusals, 400 for input the caller got wrong, 404 for a diff or workspace that is
not there, 409 for state that changed underfoot (a stop, an already-applied
diff), 429 for the model provider.
"""

from __future__ import annotations


class KiloRefused(Exception):
    """Base of every refusal from :mod:`body.mouth.kilo`."""


class DecisionRefused(KiloRefused):
    """No decision, or one that does not permit this call.

    Raised for a missing decision, one that does not allow execution, and one
    issued for a different category or target. The last is the replay case: a
    confirmation granted for "generate into demo" must not be spendable on
    "generate into production", or a single approved dialog becomes a
    general-purpose permission.
    """


class DecisionReplayRefused(DecisionRefused):
    """The decision is genuine but for another operation or another target."""


class DryRunRefused(KiloRefused):
    """The decision is a dry run, and a dry run does not execute."""


class KillSwitchRefused(DecisionRefused):
    """The emergency stop engaged after the decision was issued.

    Checked before the decision's own verdict, so a refusal that happens during a
    stop is reported as a stop rather than as an unconfirmed permission.
    """


class WorkspaceRefused(KiloRefused):
    """The target is inside ZUZZ itself, or is otherwise not a workspace.

    This is the refusal that makes the design safe rather than merely gated, and it
    is deliberately a **refusal and not a clamp**. A silently relocated write is a
    write somewhere the user did not approve, and "I redirected your request to
    somewhere safer" is how an assistant teaches a user to stop reading its output.

    Nothing here is recoverable by raising the trust level. It is enforced
    structurally: the path is never offered to the model, so it cannot be asked for.
    """


class WorkspaceConfirmationRequired(KiloRefused):
    """A workspace outside the configured root is unknown and unapproved.

    The one refusal with a confirmation path, and named for what it is. ZUZZ may
    work in a project folder the user has not registered; it may not do so until
    the user has said this folder is fine.
    """

    def __init__(self, message: str, workspace: str) -> None:
        """Record the workspace that would need approving.

        Args:
            message: What is wrong, in one line.
            workspace: The absolute path being asked about.
        """
        super().__init__(message)
        self.workspace = workspace


class WorkspaceNotFound(KiloRefused):
    """No such workspace, or it is not a directory."""


class AgentRefused(KiloRefused):
    """An agent name this package does not have."""


class ModelUnavailable(KiloRefused):
    """Neither the cloud provider nor the local fallback could answer.

    Distinct from every other refusal because it is the one that may pass: a
    provider rate-limiting is not a decision, and the local fallback exists
    precisely so that a 429 becomes a slower answer rather than a failure.
    """


class DiffRefused(KiloRefused):
    """The proposed change set cannot be built, previewed or applied as asked."""


class DiffNotFound(DiffRefused):
    """No pending diff with that id.

    Raised rather than returning an empty result because a stale id after a
    restart is a real answer: nothing is waiting to be applied.
    """


class DiffAlreadyApplied(DiffRefused):
    """This diff was already applied, so there is nothing left to roll back."""


class GitRefused(KiloRefused):
    """The git operation is refused at every trust level.

    Force-push, ``reset --hard``, ``clean -fd`` and ``push --mirror`` land here.
    A trust row can say how often to ask; it cannot say "except when it would
    destroy work", so the destructive half is refused in code instead.
    """


class CommandRefused(KiloRefused):
    """The command is not one this package will run, or would not finish."""


class CommandTimedOut(CommandRefused):
    """The command exceeded its timeout and was stopped."""

    def __init__(self, message: str, command: str, seconds: float) -> None:
        """Record what timed out and how long it was given.

        Args:
            message: What happened, in one line.
            command: The argument vector that was run.
            seconds: The timeout that was exceeded.
        """
        super().__init__(message)
        self.command = command
        self.seconds = seconds


class InstallRefused(KiloRefused):
    """The package name is unusable, or the install was not approved.

    ``install_packages`` asks at every trust level, so the second case is the
    common one and it is a refusal rather than a silent skip: the user said no,
    and reporting nothing would let the agent report the task as done.
    """


class PackageNameInvalid(InstallRefused):
    """The package name is not a plain identifier.

    Checked rather than escaped, because the command is never a shell string: a
    name containing ``;`` or ``&&`` is not a package, it is an attempt to make the
    argument vector say something other than what it appears to say.
    """
