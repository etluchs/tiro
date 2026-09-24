"""A Map of Content for each configured folder (ITERATION-2 M5).

A vault of nine hundred notes and a hundred links is reachable only by search.
An index note per context folder is the cheapest thing that makes it
navigable, and unlike ``connect`` it asserts no relationship it cannot justify
from the filesystem: these notes are in this folder, and here is each one's
opening line.

**Indexes are boring** (ITERATION-2, acceptance criterion 4):

- Deterministic. Sorted, no dates, no counts in the header, nothing that moves
  unless the folder did. Running twice changes nothing and commits nothing.
- One line per note, so adding a note is a one-line diff.
- Generated into a Tiro block, so the user may write around it and ``tiro
  strip`` removes it. Edits *inside* the block are overwritten; that is what
  the callout says.
- No model. The description is the note's own first heading or line.

Two courtesies. An index is created only where the folder is L3 — creating a
note is L3 — and only updated elsewhere. And an index the user deleted is not
recreated: Tiro remembers it made one, and a missing one is an answer.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from tiro import protocol
from tiro.config import Config, as_vault_path
from tiro.scan import is_daily, is_hidden

KEY = "tiro/index"
BLOCK_ID = "index"
DESCRIPTION_WIDTH = 80

_WIKILINK = re.compile(r"\[\[([^\]|#]*)(?:#[^\]|]*)?(?:\|([^\]]*))?\]\]")
_MDLINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


@dataclass(frozen=True)
class Outcome:
    folder: str
    note: str
    what: str  # created | updated | unchanged | skipped | declined
    detail: str = ""


def folder_key(folder: str) -> str:
    return as_vault_path(folder).strip("/")


def note_for(config: Config, folder: str) -> str:
    name = PurePosixPath(folder_key(folder)).name
    return f"{folder_key(folder)}/{config.index.note.format(name=name)}.md"


def describe(text: str, stem: str) -> str:
    """The note's opening line, as the index shows it. A first heading that
    only repeats the filename says nothing, so the line after it is used."""
    _, body, _ = protocol.split_frontmatter(text)
    body = protocol.without_code(protocol.strip_blocks(body))
    for raw in body.splitlines():
        line = raw.strip().lstrip("#>-*+ ").strip()
        if not line or line.startswith(("<!--", "|", "---")):
            continue
        line = _WIKILINK.sub(lambda m: m.group(2) or m.group(1), line)
        line = _MDLINK.sub(r"\1", line)
        line = re.sub(r"[*_`]", "", line).strip()
        if not line or line.lower() == stem.lower():
            continue
        if len(line) > DESCRIPTION_WIDTH:
            line = line[: DESCRIPTION_WIDTH - 1].rstrip() + "…"
        return line
    return "(empty)"


def build(config: Config, folder: str) -> str:
    """The block body for one folder."""
    root = config.vault / folder_key(folder)
    own = note_for(config, folder)
    groups: dict[str, list[str]] = {}
    dailies = 0
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(config.vault).as_posix()
        if rel == own or is_hidden(path.relative_to(config.vault).parts):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if protocol.read_keys(text).get(KEY):
            continue  # another folder's index; indexes do not index indexes
        if is_daily(rel):
            dailies += 1
            continue
        sub = PurePosixPath(path.relative_to(root).as_posix()).parent.as_posix()
        link = rel[:-3]
        groups.setdefault("" if sub == "." else sub, []).append(
            f"- [[{link}|{path.stem}]] — {describe(text, path.stem)}")

    lines = [
        f"> [!abstract] Tiro · index · `{folder_key(folder)}/`",
        "> Kept by Tiro and rebuilt when the folder changes. Write around this "
        "block, not inside it.",
        "",
    ]
    lines += groups.pop("", [])
    for sub in sorted(groups):
        lines += ["", f"### {sub}", *groups[sub]]
    if dailies:
        lines += ["", f"*Plus {dailies} daily notes, not listed.*"]
    if len(lines) == 3:
        lines.append("*Nothing here yet.*")
    return "\n".join(lines)


def run(config: Config, state: dict, *, today: str, now: float | None = None) -> list[Outcome]:
    """Bring every configured index up to date. Writes files; commits nothing —
    the caller commits each changed note on its own."""
    now = time.time() if now is None else now
    memory = state.setdefault("index", {})
    created = memory.setdefault("created", {})
    declined = memory.setdefault("declined", {})
    wrote = memory.setdefault("mtime", {})
    out: list[Outcome] = []

    for folder in config.index.folders:
        key = folder_key(folder)
        rel = note_for(config, folder)
        path = config.vault / rel
        if not key or not (config.vault / key).is_dir():
            out.append(Outcome(key, rel, "skipped", "no such folder"))
            continue
        if config.trust.level_for(rel) == "L0":
            out.append(Outcome(key, rel, "skipped", "the folder is L0"))
            continue

        if not path.exists():
            if rel in created:
                # Tiro made one and it is gone: the user's answer. Said once.
                if rel not in declined:
                    declined[rel] = today
                    out.append(Outcome(key, rel, "declined",
                                       "you deleted it, so Tiro will not make another; "
                                       "take the folder out of [index] to stop tracking it"))
                continue
            if not config.trust.permits(rel, "L3"):
                out.append(Outcome(key, rel, "skipped",
                                   f"creating a note needs L3; `{key}/` is "
                                   f"{config.trust.level_for(rel)}"))
                continue
            text = f"---\n{KEY}: {key}/\n---\n"
            text = protocol.upsert_block(text, job="index", id=BLOCK_ID,
                                         body=build(config, folder))
            path.write_text(text, encoding="utf-8")
            created[rel] = today
            wrote[rel] = path.stat().st_mtime
            out.append(Outcome(key, rel, "created"))
            continue

        mtime = path.stat().st_mtime
        if mtime != wrote.get(rel) and now - mtime < config.run.skip_recent_seconds:
            # Recent, and not Tiro's own last write: the user is in it.
            out.append(Outcome(key, rel, "skipped", "you are editing it"))
            continue
        text = path.read_text(encoding="utf-8")
        new = protocol.upsert_block(text, job="index", id=BLOCK_ID, body=build(config, folder))
        if protocol.read_keys(new).get(KEY) != f"{key}/":
            new = protocol.set_key(new, KEY, f"{key}/")
        if new == text:
            out.append(Outcome(key, rel, "unchanged"))
            continue
        tmp = path.with_suffix(".md.tiro-tmp")
        tmp.write_text(new, encoding="utf-8")
        tmp.replace(path)
        created.setdefault(rel, today)
        wrote[rel] = path.stat().st_mtime
        out.append(Outcome(key, rel, "updated"))
    return out


def is_index(text: str) -> bool:
    return bool(protocol.read_keys(text).get(KEY))
