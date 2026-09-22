"""Finding the work: frontmatter in, jobs out (DESIGN section 4.2)."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from tiro import protocol
from tiro.config import Config

IGNORED_DIRS = {"node_modules"}

#: ``Tiro/`` is Tiro's own surface — journal, questions, health. Nothing there
#: is a request, and the health report quotes tag syntax it must not act on.
#: It is still part of the vault (links to it resolve, notes may be filed
#: there), so it is skipped by the scan and not by ``is_hidden``.
OURS = "Tiro"

#: A daily note, by Obsidian's default naming. These are a class of their own:
#: they live where the daily-notes plugin puts them, they are never filed
#: anywhere else, and an empty one is the plugin's doing, not a leftover.
DAILY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def is_hidden(rel_parts: tuple[str, ...]) -> bool:
    """Any dot-directory is Obsidian's, a plugin's, or ours — never a note.

    An explicit list went stale as soon as a second plugin appeared; every
    dot-directory is the honest default.
    """
    return any(p.startswith(".") for p in rel_parts) or (
        len(rel_parts) > 1 and rel_parts[0] in IGNORED_DIRS
    )


def is_daily(rel: str | Path) -> bool:
    return bool(DAILY.match(Path(rel).stem))


@dataclass(frozen=True)
class Job:
    rel: str
    verb: str
    note_id: str | None
    hash_before: str
    mtime: float
    reason: str

    @property
    def valid_verb(self) -> bool:
        return self.verb in protocol.VERBS


@dataclass(frozen=True)
class Skipped:
    rel: str
    why: str


def iter_notes(vault: Path):
    for path in sorted(vault.rglob("*.md")):
        parts = path.relative_to(vault).parts
        if is_hidden(parts) or (len(parts) > 1 and parts[0] == OURS):
            continue
        yield path


def scan(config: Config, *, now: float | None = None) -> tuple[list[Job], list[Skipped]]:
    """Every note asking for work, and why the near-misses were passed over.

    Skips are returned rather than swallowed: a note that was ignored because
    the user had it open two seconds ago is a thing the journal should be able
    to say out loud.
    """
    now = time.time() if now is None else now
    jobs: list[Job] = []
    skipped: list[Skipped] = []
    for path in iter_notes(config.vault):
        rel = path.relative_to(config.vault).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            skipped.append(Skipped(rel, f"unreadable: {exc}"))
            continue
        verb = protocol.verb(text)
        if verb is None:
            continue
        if config.trust.level_for(rel) == "L0":
            # L0 is "Tiro does not touch this". A tag inside is a note, not a
            # request, and the refusal goes in the journal, never in the note.
            skipped.append(Skipped(rel, f"in an L0 folder; Tiro does not write there"))
            continue
        status = protocol.read_keys(text).get("tiro/status", "")
        if status == "needs-input" and not protocol.needs_work(text):
            skipped.append(Skipped(rel, "waiting on the user"))
            continue
        if not protocol.needs_work(text):
            continue
        age = now - path.stat().st_mtime
        if age < config.run.skip_recent_seconds:
            skipped.append(Skipped(rel, f"modified {age:.0f}s ago; user may be typing"))
            continue
        jobs.append(
            Job(
                rel=rel,
                verb=verb,
                note_id=protocol.note_id(text),
                hash_before=protocol.user_hash(text),
                mtime=path.stat().st_mtime,
                reason="new request" if "tiro/hash" not in protocol.read_keys(text)
                else "user content changed since the last run",
            )
        )
    return jobs, skipped
