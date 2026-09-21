"""Finding the work: frontmatter in, jobs out (DESIGN section 4.2)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from tiro import protocol
from tiro.config import Config

IGNORED_DIRS = {".git", ".obsidian", ".trash", ".tiro", "node_modules"}


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
        if any(part in IGNORED_DIRS for part in path.relative_to(vault).parts):
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
