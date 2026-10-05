"""Hippocampus — memory refusals.

The region's errors, so the API can map each onto the right HTTP status rather
than collapsing every failure into a 500. Three distinctions matter to a caller:

* a **refusal** (an unknown kind, an unreadable store) is the user's problem to
  fix, and is a 400;
* :class:`MemoryStoreUnavailable` is transient and worth retrying later;
* :class:`NothingToForget` is an answer, not a fault. It means the forget ran,
  found nothing older than the date given, and removed nothing — which is a
  successful no-op and not an error.

The naming convention follows the rest of the brain: everything is a
:class:`MemoryRefused`, and the API turns the base class into a 400 while
anything ending ``Unavailable`` becomes a 503.
"""

from __future__ import annotations


class MemoryRefused(Exception):
    """Base class for every Hippocampus refusal."""


class UnknownMemoryKind(MemoryRefused):
    """A caller named a kind ZUZZ does not keep.

    Distinct from a malformed record: this is about the *request*, and it happens
    before anything is written. Falling back to ``conversation`` instead would
    silently file a preference under a transcript, and the mistake would not
    surface until someone tried to read it back.
    """


class MemoryStoreUnavailable(MemoryRefused):
    """The memory store could not be read or written.

    A full disk, a locked file on Windows, or a directory that has been moved.
    Transient in a way the other refusals are not, and worth retrying rather than
    treating as a bad request.

    Attributes:
        path: The file involved, so the operator does not have to go looking.
    """

    def __init__(self, message: str, path: str = "") -> None:
        """Record the message and the path involved.

        Args:
            message: Human-readable explanation.
            path: The store path that could not be used.
        """
        super().__init__(message)
        self.path = path


class NothingToForget(MemoryRefused):
    """A forget ran, matched nothing, and removed nothing.

    An answer rather than a fault. The alternative — raising, or reporting a
    count without saying it was zero — would make ``DELETE /api/memory/forget``
    look like it had failed on every call that was simply too early.
    """


class QueryTooShort(MemoryRefused):
    """A recall query carries no searchable term.

    With no usable term the ranking has nothing to rank by and every memory ties.
    Returning an arbitrary slice of the store would be worse than refusing: a
    caller asking "what do you remember?" would get ten unrelated things and read
    it as an answer.
    """