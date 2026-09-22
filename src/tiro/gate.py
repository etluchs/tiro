"""The gate: what must be true before a job is allowed to become a commit.

Policy is checked twice — once before a tool runs, once here against the actual
working tree. The first is a decision; this is a fact. A job that fails the gate
is rolled back and its note marked ``blocked``, never committed and never
silently half-applied (DESIGN section 5.2, rules/safety.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from tiro import protocol
from tiro.config import Config, as_vault_path
from tiro.ops import BrokenLink, VaultOps
from tiro.vcs import Git

#: The trust level each verb needs to *write* a path. Every verb here writes a
#: block and ``tiro/*`` keys on the note and nothing else, so every verb is L2.
#: ``spec`` and ``dispatch`` were L3 for a while, on the theory that a spec is a
#: new note; it is not, it is a block on the note the user tagged.
REQUIRED_TRUST = {
    "triage": "L2",
    "file": "L2",
    "research": "L2",
    "distill": "L2",
    "spec": "L2",
    "dispatch": "L2",
}
MAY_MOVE = {"file"}

#: The user's tag is the consent. A note carrying ``tiro:`` is L2 for itself,
#: whatever its folder says, unless the folder is L0 — L0 is "Tiro does not
#: touch this", and a tag inside it is treated as a note, not a request.
#: Without this rule the default ladder blocks every request outside a listed
#: folder, and a vault with no structure has no folders to list.
TAGGED_NOTE_TRUST = "L2"

#: A move is two permissions, not one, and they are not the same permission.
#:
#: Taking a note *out of* where the user put it is the risky half — that is what
#: breaks the graph and loses things. But every move Tiro makes today is one the
#: user asked for by writing ``tiro: file`` on the note, and the constitution's
#: rule is "never outside L4 *without an explicit accept*". The tag is the
#: accept, so the source need only be somewhere Tiro may act at all: not L0.
#: L4 keeps its meaning for moves Tiro would initiate itself, of which there
#: are none yet.
#:
#: Putting a note somewhere is ordinary creation, so the destination must be
#: L3. Filing *into* an area is opt-in per folder: an area stays below L3 until
#: the user says Tiro may put notes there, and until then `file` blocks with a
#: message naming the folder and the level.
MOVE_FROM_TRUST = "L1"
MOVE_TO_TRUST = "L3"

#: Tiro's own state directory. Not vault content: the run ledger, run records,
#: and the payload a dispatch would post. It is committed by the journal step
#: under its own trailer, so a job writing there is bookkeeping rather than an
#: edit to the user's vault — and keeping it out of the containment check is
#: what lets a job leave an audit trail without declaring a path that has
#: nothing to do with the note it is working on.
OURS = ".tiro/"


@dataclass(frozen=True)
class Snapshot:
    """The world as it was before the job ran."""

    head: str
    dirty: tuple[str, ...]
    broken: frozenset[tuple[str, str]]
    note_mtime: float


@dataclass
class GateResult:
    ok: bool
    failures: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)

    def fail(self, why: str) -> None:
        self.ok = False
        self.failures.append(why)


def _broken_set(links: list[BrokenLink]) -> frozenset[tuple[str, str]]:
    return frozenset((b.note, b.target) for b in links)


def _is_ours(path: str) -> bool:
    return as_vault_path(path).startswith(OURS)


def destination_problem(config: Config, destination: str) -> str | None:
    """Why a `file` destination is unacceptable, or None.

    The destination comes from the note — written by triage from what it read,
    which is the injection channel the constitution warns about. So it is
    checked as a path before it is checked for trust: inside the vault, a
    markdown note, not in a hidden directory. A note filed to ``../x.md`` has
    left the vault, and one filed to ``Areas/x`` (no suffix) is a note the scan
    will never see again.
    """
    from tiro.scan import is_hidden

    rel = as_vault_path(destination)
    if not rel or rel.endswith("/"):
        return "no filename"
    if not rel.endswith(".md"):
        return "not a markdown note"
    if ".." in Path(rel).parts or Path(destination).is_absolute() or "\\" in destination:
        return "outside the vault"
    try:
        (config.vault / rel).resolve().relative_to(config.vault.resolve())
    except ValueError:
        return "outside the vault"
    if is_hidden(Path(rel).parts):
        return "in a hidden or reserved directory"
    return None


def note_trust(config: Config, note_rel: str) -> str:
    """The level a tagged note is treated at: its folder's, raised to L2 by the
    tag, unless the folder is L0."""
    level = config.trust.level_for(note_rel)
    if level == "L0":
        return level
    return max(level, TAGGED_NOTE_TRUST, key=config.trust.index)


def note_permits(config: Config, note_rel: str, required: str) -> bool:
    return config.trust.index(note_trust(config, note_rel)) >= config.trust.index(required)


def snapshot(config: Config, git: Git, ops: VaultOps, note_rel: str) -> Snapshot:
    ops.refresh()
    note = config.vault / note_rel
    return Snapshot(
        head=git.head(),
        dirty=tuple(git.dirty_paths()),
        broken=_broken_set(ops.unresolved()),
        note_mtime=note.stat().st_mtime if note.exists() else 0.0,
    )


def check(
    config: Config,
    git: Git,
    ops: VaultOps,
    *,
    verb: str,
    note_rel: str,
    declared: list[str],
    before: Snapshot,
    moved: tuple[str, str] | None = None,
    rewritten: set[str] | None = None,
) -> GateResult:
    """Validate everything the job touched. Every failure is collected, not just
    the first: a job that broke three rules should say so once."""
    result = GateResult(ok=True)
    required = REQUIRED_TRUST.get(verb, "L4")
    declared_set = {as_vault_path(d) for d in declared}
    move_src, move_dst = moved if moved else ("", "")
    # Notes whose inbound links Obsidian rewrote as part of this move. They
    # changed, and they were declared, but the level they are judged at is the
    # move's, not the write rule's: a link rewrite is a consequence of a move
    # the user authorised, not Tiro editing somebody's prose. Judging them as
    # writes would make filing any linked note out of an L1 folder impossible,
    # which is most notes worth filing.
    relinked = {as_vault_path(p) for p in (rewritten or set())}

    if moved and verb not in MAY_MOVE:
        result.fail(f"`{verb}` moved a note; only `file` may do that")
    if moved:
        problem = destination_problem(config, move_dst)
        if problem:
            result.fail(f"cannot file to {move_dst}: {problem}")
        if not config.trust.permits(move_src, MOVE_FROM_TRUST):
            result.fail(
                f"cannot move a note out of {move_src}: that folder is "
                f"{config.trust.level_for(move_src)}, and moving out needs "
                f"{MOVE_FROM_TRUST}"
            )
        if not config.trust.permits(move_dst, MOVE_TO_TRUST):
            result.fail(
                f"cannot file into {move_dst}: that folder is "
                f"{config.trust.level_for(move_dst)}, and filing needs "
                f"{MOVE_TO_TRUST}"
            )

    changed = [
        p for p in git.dirty_paths() if p not in before.dirty and not _is_ours(p)
    ]
    result.changed = changed

    # 2. containment, and 6-by-proxy: anything we did not declare is a bug.
    for path in changed:
        if path not in declared_set:
            result.fail(f"changed an undeclared path: {path}")
        if path in (move_src, move_dst) or path in relinked:
            continue  # judged by the move rules above, not by the write rule
        # The tagged note itself is judged with its tag counted as consent;
        # any other path the job touched is judged by its folder alone.
        allowed = (note_permits(config, path, required) if path == note_rel
                   else config.trust.permits(path, required))
        if not allowed:
            result.fail(
                f"{verb} needs {required} for {path}, which is "
                f"{config.trust.level_for(path)}"
            )

    # 3 and 4. deletions and moves.
    for line in git("status", "--porcelain", "-z").split("\0"):
        if len(line) <= 3:
            continue
        code, path = line[:2], line[3:]
        if path in before.dirty or _is_ours(path):
            continue
        if "D" in code and path != move_src:
            result.fail(f"deleted {path}; Tiro never deletes a note")
        if code.startswith("R") and path != move_src:
            result.fail(f"renamed {path}; only a declared `file` move may do that")

    # 1. the note still conforms.
    note = config.vault / (move_dst if moved else note_rel)
    if note.exists():
        text = note.read_text(encoding="utf-8", errors="replace")
        keys = protocol.read_keys(text)
        status = keys.get("tiro/status")
        if status and status not in protocol.STATUSES:
            result.fail(f"invalid tiro/status: {status!r}")
        for block in protocol.find_blocks(text):
            if not block.id:
                result.fail("a Tiro block has no id")
        if "<!-- tiro:begin" in text and len(protocol.find_blocks(text)) == 0:
            result.fail("a Tiro block was left unclosed")
    elif note_rel != move_src:
        result.fail(f"the note disappeared: {note_rel}")

    # 5. link integrity. A link that resolved before must resolve now.
    ops.refresh()
    after = _broken_set(ops.unresolved())
    for note_path, target in sorted(after - before.broken):
        # The Obsidian backend reports the broken target without the note it
        # sits in, so say so rather than printing "in " and a blank.
        where = f" in {note_path}" if note_path else " (somewhere in the vault)"
        result.fail(f"broke a link: [[{target}]]{where}")

    return result


def rollback(git: Git, declared: list[str], created: list[str] | None = None) -> None:
    """Undo a job. ``created`` names paths Tiro itself made this run — only
    those may be removed; everything else is merely restored."""
    git.discard(created or [])
    git.restore(declared)
