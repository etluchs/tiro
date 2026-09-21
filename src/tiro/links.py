"""Wikilink parsing and resolution — the filesystem backend's approximation.

Obsidian's own metadata cache is the ground truth for what a link resolves to,
and where the Obsidian CLI is available we ask it instead (see ``ops.py``). This
module exists for the case where it is not, and for the gate's before/after
comparison, which only needs to be *consistent* rather than perfect: a link that
resolved before a job must still resolve after it, by whatever rule we used both
times.

Where it approximates, it says so rather than pretending: ``resolve`` returns
``None`` for anything it cannot place, and lint labels its report accordingly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from tiro.config import as_vault_path

_WIKILINK = re.compile(r"(!?)\[\[([^\[\]|#^]*)((?:[#^][^\[\]|]*)?)(?:\|([^\[\]]*))?\]\]")
_MD_LINK = re.compile(r"(!?)\[([^\]]*)\]\(([^)\s]+?)(?:\s+\"[^\"]*\")?\)")
_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class Link:
    target: str  # as written, without the #subpath or |alias
    subpath: str  # "#heading" or "#^block", or ""
    alias: str  # display text, or ""
    embed: bool
    line: int  # 1-indexed
    wiki: bool  # wikilink rather than a markdown link


def _code_free_lines(text: str) -> list[tuple[int, str]]:
    """Lines outside fenced code, with inline code spans blanked.

    A link inside a code block is an example, not a link; treating it as one
    produces phantom broken links and, worse, phantom rewrites.
    """
    out: list[tuple[int, str]] = []
    fence: str | None = None
    for i, line in enumerate(text.split("\n"), start=1):
        m = _FENCE.match(line)
        if m:
            token = m.group(1)
            if fence is None:
                fence = token
            elif line.strip().startswith(fence):
                fence = None
            continue
        if fence is not None:
            continue
        out.append((i, re.sub(r"`[^`]*`", lambda m: " " * len(m.group(0)), line)))
    return out


def iter_links(text: str) -> list[Link]:
    links: list[Link] = []
    for lineno, line in _code_free_lines(text):
        for m in _WIKILINK.finditer(line):
            target = m.group(2).strip()
            if not target and not m.group(3):
                continue
            links.append(
                Link(
                    target=target,
                    subpath=m.group(3) or "",
                    alias=(m.group(4) or "").strip(),
                    embed=m.group(1) == "!",
                    line=lineno,
                    wiki=True,
                )
            )
        for m in _MD_LINK.finditer(line):
            href = m.group(3)
            if "://" in href or href.startswith(("#", "mailto:")):
                continue
            links.append(
                Link(
                    target=href,
                    subpath="",
                    alias=m.group(2),
                    embed=m.group(1) == "!",
                    line=lineno,
                    wiki=False,
                )
            )
    return links


class Index:
    """A vault-wide lookup for link resolution.

    Mirrors Obsidian's precedence as closely as is reasonable: an exact
    vault-relative path first, then a unique basename, then an alias, then —
    when a basename is ambiguous — the candidate closest to the linking note.
    """

    def __init__(self, vault: Path) -> None:
        self.vault = vault
        self.paths: set[str] = set()
        self._by_rel: dict[str, str] = {}
        self._by_stem: dict[str, list[str]] = {}
        self._by_alias: dict[str, list[str]] = {}
        for path in sorted(vault.rglob("*")):
            if not path.is_file() or _ignored(path, vault):
                continue
            rel = path.relative_to(vault).as_posix()
            self.paths.add(rel)
            self._by_rel[rel.lower()] = rel
            if rel.lower().endswith(".md"):
                self._by_rel[rel[:-3].lower()] = rel
            self._by_stem.setdefault(path.stem.lower(), []).append(rel)
        for rel in list(self.paths):
            if rel.endswith(".md"):
                for alias in _aliases((vault / rel).read_text(encoding="utf-8", errors="replace")):
                    self._by_alias.setdefault(alias.lower(), []).append(rel)

    def resolve(self, target: str, *, from_rel: str = "") -> str | None:
        t = as_vault_path(target.strip()).lstrip("./")
        if not t:
            return from_rel or None
        key = t.lower()
        if key in self._by_rel:
            return self._by_rel[key]
        if key + ".md" in self._by_rel:
            return self._by_rel[key + ".md"]
        if "/" in t:
            # A relative path from the linking note, as markdown links use.
            if from_rel:
                joined = (Path(from_rel).parent / t).as_posix().lower()
                for candidate in (joined, joined + ".md"):
                    if candidate in self._by_rel:
                        return self._by_rel[candidate]
            return None
        for bucket in (self._by_stem.get(key), self._by_alias.get(key)):
            if bucket:
                return _closest(bucket, from_rel)
        return None

    def unresolved(self) -> list[tuple[str, Link]]:
        """Every link in the vault that does not resolve."""
        out: list[tuple[str, Link]] = []
        for rel in sorted(self.paths):
            if not rel.endswith(".md"):
                continue
            text = (self.vault / rel).read_text(encoding="utf-8", errors="replace")
            for link in iter_links(text):
                if self.resolve(link.target, from_rel=rel) is None:
                    out.append((rel, link))
        return out

    def backlinks(self, rel: str) -> list[str]:
        out: list[str] = []
        for other in sorted(self.paths):
            if not other.endswith(".md") or other == rel:
                continue
            text = (self.vault / other).read_text(encoding="utf-8", errors="replace")
            for link in iter_links(text):
                if self.resolve(link.target, from_rel=other) == rel:
                    out.append(other)
                    break
        return out


def _closest(candidates: list[str], from_rel: str) -> str:
    if len(candidates) == 1 or not from_rel:
        return sorted(candidates)[0]
    here = Path(from_rel).parent.as_posix()

    def distance(rel: str) -> tuple[int, str]:
        there = Path(rel).parent.as_posix()
        shared = 0
        for a, b in zip(here.split("/"), there.split("/")):
            if a != b:
                break
            shared += 1
        return (-shared, rel)

    return sorted(candidates, key=distance)[0]


def _aliases(text: str) -> list[str]:
    from tiro.protocol import split_frontmatter

    fm, _, present = split_frontmatter(text)
    if not present:
        return []
    out: list[str] = []
    collecting = False
    for line in fm:
        if re.match(r"^alias(es)?\s*:", line):
            value = line.split(":", 1)[1].strip()
            if value.startswith("["):
                out += [v.strip().strip("\"'") for v in value.strip("[]").split(",") if v.strip()]
            elif value:
                out.append(value.strip("\"'"))
            else:
                collecting = True
            continue
        if collecting:
            if line.startswith((" ", "\t", "-")):
                item = line.lstrip(" \t-").strip().strip("\"'")
                if item:
                    out.append(item)
                continue
            collecting = False
    return out


def _ignored(path: Path, vault: Path) -> bool:
    parts = path.relative_to(vault).parts
    return any(p in (".git", ".obsidian", ".trash", ".tiro") for p in parts)
