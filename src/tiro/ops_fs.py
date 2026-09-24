"""Filesystem backend: our own link resolution, honest about being approximate."""

from __future__ import annotations

from pathlib import Path

from tiro.links import Index, iter_links
from tiro.ops import BrokenLink, OpsDown


class FilesystemOps:
    name = "filesystem"
    authoritative = False

    def __init__(self, vault: Path) -> None:
        self.vault = vault
        self._index: Index | None = None

    @property
    def index(self) -> Index:
        if self._index is None:
            self._index = Index(self.vault)
        return self._index

    def refresh(self) -> None:
        self._index = None

    def unresolved(self) -> list[BrokenLink]:
        return [
            BrokenLink(note=rel, target=link.target, line=link.line)
            for rel, link in self.index.unresolved()
        ]

    def orphans(self) -> list[str]:
        linked: set[str] = set()
        for rel in self.index.paths:
            if not rel.endswith(".md"):
                continue
            text = (self.vault / rel).read_text(encoding="utf-8", errors="replace")
            for link in iter_links(text):
                target = self.index.resolve(link.target, from_rel=rel)
                if target and target != rel:
                    linked.add(target)
        return sorted(r for r in self.index.paths if r.endswith(".md") and r not in linked)

    def deadends(self) -> list[str]:
        out = []
        for rel in sorted(self.index.paths):
            if not rel.endswith(".md"):
                continue
            text = (self.vault / rel).read_text(encoding="utf-8", errors="replace")
            if not any(
                self.index.resolve(l.target, from_rel=rel) not in (None, rel)
                for l in iter_links(text)
            ):
                out.append(rel)
        return out

    def backlinks(self, rel: str) -> list[str]:
        return self.index.backlinks(rel)

    def move(self, src_rel: str, dst_rel: str) -> None:
        # Moving without rewriting inbound links breaks the graph silently, and
        # rewriting them correctly means reimplementing Obsidian's resolution
        # rules. Iteration 1 declines to do either (DESIGN section 3.4).
        # Obsidian closed is the machine's state, not the note's: the move will
        # work on the first run after it is open again.
        raise OpsDown(
            "moving a note needs the Obsidian CLI, so that Obsidian rewrites "
            "the inbound links itself"
        )
