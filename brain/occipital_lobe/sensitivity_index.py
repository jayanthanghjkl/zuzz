"""Occipital Lobe — which stored screenshots carry credentials.

A sidecar index beside the screenshots, so the viewer can tell which files must not
be opened casually without reading a log.

It is a file rather than the audit log because the audit log is append-only and
this needs to be *readable*: :meth:`SensitivityIndex.flagged` is called by the
listing endpoint on every request, and filtering an append-only JSONL for it would
mean reimplementing the log format inside a screenshot directory. The audit record
of the classification still goes to the audit log; this is the index the UI reads.

The file holds file **stems**, not names, and not paths. A stem is what both the
listing and the viewer already hold, so nothing here can construct a path, and a
tampered index can at worst cause a screenshot to be mislabelled as sensitive —
which closes rather than opens.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Set

#: Kept beside the PNGs rather than inside ``storage/logs/``, because its whole
#: purpose is to travel with the thing it describes.
FILENAME = "sensitive.txt"


class SensitivityIndex:
    """The set of stored screenshots flagged as carrying credentials.

    Attributes:
        root: The screenshot directory the index lives in.
    """

    def __init__(self, root: Path) -> None:
        """Bind the index to a screenshot directory.

        Args:
            root: The directory holding the PNGs.
        """
        self.root = Path(root)

    @property
    def path(self) -> Path:
        """Return the index file's path."""
        return self.root / FILENAME

    def flagged(self) -> Set[str]:
        """Return the stems of every screenshot flagged as sensitive.

        Returns:
            A set of file stems. Empty when the index does not exist, which is the
            normal state on a machine that has never captured a login screen.
        """
        if not self.path.is_file():
            return set()
        return {
            line.strip()
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }

    def flag(self, stems: Iterable[str], sensitive: bool = True) -> None:
        """Record, or clear, that some screenshots carry credentials.

        Args:
            stems: File stems, as :meth:`Path.stem` reports them.
            sensitive: False to clear the flags instead of setting them.

        Returns:
            Nothing. Writing the whole file each time keeps it readable and
            self-repairing; a screenshot store is small and the operation is rare.
        """
        current = self.flagged()
        for stem in stems:
            if sensitive:
                current.add(str(Path(str(stem)).stem))
            else:
                current.discard(str(Path(str(stem)).stem))
        self.root.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            "".join(f"{stem}\n" for stem in sorted(current)), encoding="utf-8"
        )