"""Undo for one job: put back what Tiro changed, and only that.

Rollback used to be ``git checkout``, which restores HEAD. But HEAD is not what
the vault looked like before the job. It lacks every edit the user has not
committed — and nothing in Obsidian commits for them, so that is most edits —
and the working tree is shared with a person typing in Obsidian while the job
runs. Restoring HEAD therefore undid the user along with Tiro: a failed job
reverted the question they had just added, and the guard that noticed them
typing in the note threw away the typing it had noticed.

So a job keeps its own record instead. For every file it is about to change it
remembers the bytes as they were (``track``), and after changing them the bytes
it left (``settle``). Undo then puts a file back only if it is still exactly as
Tiro left it. A file that has changed since is someone else's now — the user
saving, or Obsidian doing something nobody predicted — and Tiro cannot tell
which, so it leaves the file as it finds it and says so.

This is also why undo can remove a file without breaking the first rule: only a
file Tiro's own record shows did not exist before the job, and which nobody has
touched since. That is a copy Tiro made, not a note anyone wrote.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: A file Tiro tracked but never settled: it knows what was there before, but
#: not what it left, because the change it was making did not finish.
_UNSETTLED = object()


@dataclass
class _Entry:
    before: bytes | None  # None: the file did not exist
    mtime: float | None
    after: object = _UNSETTLED  # bytes, None (Tiro left it absent) or unsettled


class Undo:
    def __init__(self, vault: Path) -> None:
        self.vault = vault
        self._entries: dict[str, _Entry] = {}

    def _read(self, rel: str) -> bytes | None:
        path = self.vault / rel
        return path.read_bytes() if path.is_file() else None

    def track(self, rel: str) -> None:
        """Remember ``rel`` as it is now, before Tiro changes it. Only the first
        call counts: "before" means before the job, not before the last write."""
        if rel in self._entries:
            return
        path = self.vault / rel
        before = self._read(rel)
        mtime = path.stat().st_mtime if before is not None else None
        self._entries[rel] = _Entry(before=before, mtime=mtime)

    def settle(self, rel: str) -> None:
        """Remember ``rel`` as Tiro has just left it."""
        self.track(rel)
        self._entries[rel].after = self._read(rel)

    def write(self, rel: str, text: str, *, keep_mtime: float | None = None) -> None:
        """Write a note the way every Tiro write goes: recorded, then atomic."""
        self.track(rel)
        _atomic(self.vault / rel, text.encode("utf-8"), keep_mtime)
        self.settle(rel)

    def undo(self) -> list[str]:
        """Put back every file still as Tiro left it. Returns the ones left
        alone because something else has changed them since."""
        left_alone: list[str] = []
        for rel, entry in reversed(list(self._entries.items())):
            now = self._read(rel)
            if now == entry.before:
                continue  # already as it was, or never really changed
            if entry.after is _UNSETTLED or now != entry.after:
                left_alone.append(rel)
                continue
            path = self.vault / rel
            if entry.before is None:
                path.unlink()  # a copy Tiro made and nobody has touched
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                _atomic(path, entry.before, entry.mtime)
        return sorted(left_alone)


def _atomic(path: Path, data: bytes, keep_mtime: float | None) -> None:
    """Temp file plus rename, so Obsidian never sees half a note. ``keep_mtime``
    puts the modification time back, so Tiro's own write does not look like the
    user typing."""
    tmp = path.with_suffix(path.suffix + ".tiro-tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    if keep_mtime is not None:
        os.utime(path, (keep_mtime, keep_mtime))
