"""The correction log.

These tests are mostly about what must *not* be logged. A correction that never
happened teaches Tiro a rule nobody wants, and `reflect` has no way to tell a
bad line from a good one — so the false-positive cases matter more here than
the positive ones.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tiro import corrections, protocol, runner
from tiro.agent import JobOutput, ScriptedAgent
from tiro.ops_fs import FilesystemOps

WHEN = "2026-09-22T12:00:00+00:00"


def _observe(config, state: dict | None = None) -> list[corrections.Correction]:
    return corrections.observe(config, state or {}, now=WHEN)


def _kinds(found) -> set[str]:
    return {c.kind for c in found}


# --- the three things that are corrections -------------------------------


def test_moving_a_note_after_tiro_filed_it_is_a_correction(config, vault: Path) -> None:
    note = vault / "Areas/moved.md"
    note.write_text("---\ntiro/id: abc\ntiro/filed: uzh/moved.md\n---\n\nbody\n",
                    encoding="utf-8")

    found = _observe(config)
    correction = next(c for c in found if c.kind == "moved-after-filing")
    assert (correction.was, correction.now) == ("uzh/moved.md", "Areas/moved.md")
    assert correction.note_id == "abc"


def test_overriding_a_proposed_destination_is_a_correction(config, vault: Path) -> None:
    rel = "00 Inbox/thing.md"
    (vault / rel).write_text("---\ntiro/id: abc\ntiro/filed-to: mch/thing.md\n---\n\nbody\n",
                             encoding="utf-8")
    state = {"proposals": {rel: {"filed_to": "uzh/thing.md", "run": "r1"}}}

    correction = next(c for c in _observe(config, state) if c.kind == "overrode-proposal")
    assert (correction.was, correction.now) == ("uzh/thing.md", "mch/thing.md")
    assert correction.run == "r1"


def test_deleting_tiros_block_is_a_correction(config, vault: Path) -> None:
    rel = "00 Inbox/rejected.md"
    (vault / rel).write_text("---\ntiro/id: abc\ntiro/status: done\n---\n\nmy prose\n",
                             encoding="utf-8")
    state = {"blocks": {"abc": "r1"}}

    correction = next(c for c in _observe(config, state) if c.kind == "rejected-block")
    assert correction.note == rel
    assert correction.now == "deleted"


# --- everything that is not ----------------------------------------------


def test_an_untouched_vault_produces_nothing(config) -> None:
    assert _observe(config) == []


def test_a_proposal_the_user_has_not_answered_is_not_a_correction(config, vault: Path) -> None:
    """`triage` proposed a home and the note is still in the inbox. That is a
    pending decision, not disagreement."""
    rel = "00 Inbox/pending.md"
    (vault / rel).write_text("---\ntiro/id: abc\ntiro/filed-to: uzh/pending.md\n---\n\nbody\n",
                             encoding="utf-8")
    state = {"proposals": {rel: {"filed_to": "uzh/pending.md", "run": "r1"}}}
    assert _observe(config, state) == []


def test_a_note_sitting_where_tiro_filed_it_is_not_a_correction(config, vault: Path) -> None:
    (vault / "Areas/agreed.md").write_text(
        "---\ntiro/id: abc\ntiro/filed: Areas/agreed.md\n---\n\nbody\n", encoding="utf-8")
    assert _observe(config) == []


def test_an_id_with_no_block_is_not_a_rejection_unless_a_block_was_written(
        config, vault: Path) -> None:
    """`tiro/id` is assigned on first touch, so a job that returned an empty
    block leaves an id and no block. That is not the user deleting anything."""
    (vault / "00 Inbox/idonly.md").write_text(
        "---\ntiro/id: abc\ntiro/status: done\n---\n\nbody\n", encoding="utf-8")
    assert _observe(config, {}) == []
    assert _kinds(_observe(config, {"blocks": {"abc": "r1"}})) == {"rejected-block"}


def test_stripping_a_note_is_not_a_correction(config, vault: Path) -> None:
    """`tiro strip` removes the keys as well as the block, so nothing is left
    to diverge from."""
    rel = "00 Inbox/stripped.md"
    path = vault / rel
    path.write_text("---\ntiro/id: abc\n---\n\nbody\n\n"
                    "<!-- tiro:begin job=research id=abc -->\nx\n<!-- tiro:end id=abc -->\n",
                    encoding="utf-8")
    state = {"blocks": {"abc": "r1"}}
    assert _observe(config, state) == []  # block present

    path.write_text(protocol.strip_blocks(protocol.strip_keys(path.read_text())),
                    encoding="utf-8")
    assert _observe(config, state) == []  # and now nothing of Tiro's remains


# --- the log itself -------------------------------------------------------


def test_a_correction_is_logged_once_however_long_it_stays_true(config, vault: Path) -> None:
    """The user moves a note and leaves it there. That is one correction, not
    one per run for the rest of time."""
    (vault / "Areas/moved.md").write_text(
        "---\ntiro/id: abc\ntiro/filed: uzh/moved.md\n---\n\nbody\n", encoding="utf-8")

    assert len(corrections.log(config, _observe(config))) == 1
    assert corrections.log(config, _observe(config)) == []
    assert len(corrections.read(config)) == 1


def test_the_log_survives_a_corrupt_line(config, vault: Path) -> None:
    path = corrections.path_for(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    good = json.dumps({"kind": "moved-after-filing", "note": "a.md",
                       "was": "b.md", "now": "a.md"})
    path.write_text(f"{good}\nnot json at all\n{good}\n", encoding="utf-8")
    assert len(corrections.read(config)) == 2


def test_the_log_is_append_only_json_lines(config, vault: Path) -> None:
    (vault / "Areas/moved.md").write_text(
        "---\ntiro/id: abc\ntiro/filed: uzh/moved.md\n---\n\nbody\n", encoding="utf-8")
    corrections.log(config, _observe(config))
    lines = corrections.path_for(config).read_text().strip().split("\n")
    assert len(lines) == 1
    assert json.loads(lines[0])["kind"] == "moved-after-filing"


# --- end to end through a run --------------------------------------------


def test_a_run_notices_a_correction_and_journals_it(config, vault: Path) -> None:
    import os

    (vault / "Areas/moved.md").write_text(
        "---\ntiro/id: abc\ntiro/filed: uzh/moved.md\n---\n\nbody\n", encoding="utf-8")
    for path in vault.rglob("*.md"):
        os.utime(path, (0, 0))

    record = runner.once(config, agent=ScriptedAgent({"research": JobOutput(block="x"),
                                                      "triage": JobOutput(block="y")}),
                         ops=FilesystemOps(vault))

    assert any("noted a correction" in n for n in record.notes)
    journal = (vault / "Tiro/Journal" / f"{record.run_id[:10]}.md").read_text()
    assert "moved-after-filing" in journal
    assert len(corrections.read(config)) == 1


def test_filing_a_note_records_where_tiro_put_it(config, vault: Path) -> None:
    """Without `tiro/filed`, a later move cannot be told from a proposal the
    user never accepted."""
    import os

    rel = "00 Inbox/to-file.md"
    (vault / rel).write_text("---\ntiro: file\ntiro/filed-to: Tiro/to-file.md\n---\n\nbody\n",
                             encoding="utf-8")
    for path in vault.rglob("*.md"):
        os.utime(path, (0, 0))

    class Moving(FilesystemOps):
        def move(self, src: str, dst: str) -> None:
            (self.vault / dst).parent.mkdir(parents=True, exist_ok=True)
            (self.vault / src).rename(self.vault / dst)

    runner.once(config, agent=ScriptedAgent({
        "file": JobOutput(keys={"tiro/filed-to": "Tiro/to-file.md"}, block="x"),
        "research": JobOutput(block="x"), "triage": JobOutput(block="y")}),
        ops=Moving(vault))

    filed = protocol.read_keys((vault / "Tiro/to-file.md").read_text())
    assert filed["tiro/filed"] == "Tiro/to-file.md"
    # And sitting where it was filed is not a correction.
    assert not [c for c in _observe(config) if c.kind == "moved-after-filing"]
