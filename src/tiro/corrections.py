"""Where the user overruled Tiro (DESIGN section 7).

ITERATION-1 promised this "from day one … logging costs nothing and the data
cannot be recovered retroactively", and then did not build it. `reflect` in
iteration 2 reads what accumulates here; without it, it reads nothing.

**Observed, never inferred.** Every correction below is a diff between a record
Tiro made and what is true now. Nothing here guesses at intent, and nothing
fires on the user's ordinary editing. That restraint is the whole design: a
false positive teaches Tiro a rule nobody wants, which is worse than learning
nothing at all.

Three divergences are observable without ambiguity:

``overrode-proposal``
    `triage` proposed a destination, and the `tiro/filed-to` on the note now
    says somewhere else. The proposal is remembered in `.tiro/state.json` at
    the moment it is made, so the comparison is against Tiro's own record
    rather than against a guess.
``moved-after-filing``
    `file` put the note at a path and recorded it in `tiro/filed`. The note is
    somewhere else now, so the user moved it.
``rejected-block``
    Tiro wrote a block on a note and the block is gone. The user deleted Tiro's
    work outright, which is the loudest signal in the system and was until now
    completely invisible. Keyed on a record that a block was written, not on
    `tiro/id` alone: the id is assigned on first touch, so a job returning an
    empty block would otherwise look like a rejection.

What is deliberately *not* observed: tags. Tiro suggests tags inside its own
block, so "the user did not apply them" and "the user applied them and then
took them off" look identical without history, and only the second is a
correction. Guessing there would poison the log.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from tiro import protocol
from tiro.config import Config
from tiro.scan import iter_notes

FILENAME = "corrections.jsonl"


@dataclass(frozen=True)
class Correction:
    kind: str  # overrode-proposal | moved-after-filing | rejected-block
    note: str  # where the note is now
    was: str  # what Tiro recorded
    now: str  # what is true instead
    note_id: str = ""
    run: str = ""  # the run that made the record being corrected
    when: str = ""

    @property
    def key(self) -> tuple[str, str, str, str]:
        """Identity, so the same correction is never logged twice. The note's
        id rather than its path: a note that moved is the same note."""
        return (self.kind, self.note_id or self.note, self.was, self.now)


def path_for(config: Config) -> Path:
    return config.tiro_dir / FILENAME


def read(config: Config) -> list[Correction]:
    """Every correction logged so far. A malformed line is skipped rather than
    fatal: this is an append-only log, and losing the rest of it to one bad
    line would be the worse outcome."""
    path = path_for(config)
    if not path.exists():
        return []
    out: list[Correction] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            known = {f: data.get(f, "") for f in Correction.__dataclass_fields__}
            out.append(Correction(**known))
    return out


def remember_block(state: dict, note_id: str, run_id: str) -> None:
    """Record that a block was actually written for this note.

    `tiro/id` is assigned on first touch, before any block exists, so a job
    that returns an empty block leaves an id with no block — which is
    indistinguishable from the user having deleted one. Only a note Tiro
    knows it wrote a block on can have had that block rejected.
    """
    if note_id:
        state.setdefault("blocks", {})[note_id] = run_id


def remember_proposal(state: dict, note_rel: str, filed_to: str, run_id: str) -> None:
    """Record what `triage` proposed, at the moment it proposes it.

    Without this the log cannot tell "the user overrode the destination" from
    "Tiro proposed that destination in the first place" — the proposal lives in
    a frontmatter key the user is free to edit, so once edited, Tiro's own
    record of it is gone.
    """
    if filed_to:
        state.setdefault("proposals", {})[note_rel] = {"filed_to": filed_to, "run": run_id}


def observe(config: Config, state: dict, *, now: str | None = None) -> list[Correction]:
    """Every divergence visible in the vault right now.

    Called from the scan rather than from a job, so a note nothing is queued
    against is still observed — which is most of them, since the user corrects
    Tiro long after the job that earned the correction.
    """
    when = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    proposals = state.get("proposals", {})
    found: list[Correction] = []

    for path in iter_notes(config.vault):
        rel = path.relative_to(config.vault).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        keys = protocol.read_keys(text)
        note_id = keys.get("tiro/id", "")

        filed = keys.get("tiro/filed", "")
        if filed and filed != rel:
            found.append(Correction("moved-after-filing", rel, filed, rel,
                                    note_id, keys.get("tiro/run", ""), when))

        proposed = proposals.get(rel, {}).get("filed_to", "")
        current = keys.get("tiro/filed-to", "")
        if proposed and current and proposed != current:
            found.append(Correction("overrode-proposal", rel, proposed, current,
                                    note_id, proposals[rel].get("run", ""), when))

        wrote_block = state.get("blocks", {}).get(note_id)
        if wrote_block and not any(b.id == note_id for b in protocol.find_blocks(text)):
            found.append(Correction("rejected-block", rel, note_id, "deleted",
                                    note_id, wrote_block, when))

    return found


def log(config: Config, corrections: list[Correction]) -> list[Correction]:
    """Append the ones not already logged. Returns what was actually written.

    Append-only and deduplicated by identity, so a correction that stays true —
    a note the user moved and left there — is recorded once rather than on
    every run for the rest of time.
    """
    already = {c.key for c in read(config)}
    fresh = [c for c in corrections if c.key not in already]
    if not fresh:
        return []
    path = path_for(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for correction in fresh:
            handle.write(json.dumps(asdict(correction), sort_keys=True) + "\n")
    return fresh
