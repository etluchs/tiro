"""Undoing a job must never undo the user.

Rollback used to mean `git checkout`, which puts a file back to HEAD. HEAD is not
what the vault looked like before the job: it lacks every edit the user has not
committed, and in a vault where nothing commits for them that is most edits. And
the working tree is shared with a person typing in Obsidian, so "every file that
changed during the job" is not the same as "every file Tiro changed".

Each test here is a way the old rollback destroyed the user's writing.
"""

from __future__ import annotations

import os
from pathlib import Path

from tiro import protocol, runner
from tiro.agent import AgentError, JobOutput, ScriptedAgent
from tiro.ops_fs import FilesystemOps
from tiro.undo import Undo
from tiro.vcs import Git

NOTE = "00 Inbox/bitter-lesson.md"
BYSTANDER = "Areas/orphan.md"


def _age(vault: Path) -> None:
    for path in vault.rglob("*.md"):
        os.utime(path, (0, 0))


def _run(config, agent, ops=None):
    return runner.once(config, agent=agent, ops=ops or FilesystemOps(config.vault))


def test_typing_in_the_note_mid_job_survives(config, vault: Path) -> None:
    """The guard that notices the user typing used to "restore" the note, which
    threw away exactly the typing it had noticed."""
    path = vault / NOTE

    class Typist(ScriptedAgent):
        def run(self, request):
            if request.note_rel == NOTE:
                path.write_text(path.read_text() + "\nthe user types\n", encoding="utf-8")
            return super().run(request)

    _age(vault)
    record = _run(config, Typist({"research": JobOutput(block="x"), "triage": JobOutput(block="y")}))

    assert next(e for e in record.entries if e.note == NOTE).outcome == "skipped"
    assert "the user types" in path.read_text()


def test_an_uncommitted_edit_survives_a_failed_job(config, vault: Path) -> None:
    """The user adds a question to a committed note and does not commit — nothing
    in Obsidian does. The job fails. The question must still be there."""
    path = vault / NOTE
    path.write_text(path.read_text() + "\nMy follow-up question, not yet committed.\n",
                    encoding="utf-8")
    _age(vault)

    _run(config, ScriptedAgent({"research": AgentError("the model gave up"),
                                "triage": JobOutput(block="y")}))

    text = path.read_text()
    assert "My follow-up question, not yet committed." in text
    assert protocol.read_keys(text)["tiro/status"] == "blocked"


def test_an_edit_to_another_note_during_a_job_survives_and_the_job_completes(config, vault: Path) -> None:
    """A research job runs for minutes. The user is in Obsidian, editing some
    other note. That edit is theirs: the job must neither fail over it nor undo it."""
    other = vault / BYSTANDER

    class Busy(ScriptedAgent):
        def run(self, request):
            if request.note_rel == NOTE:
                other.write_text(other.read_text() + "the user was here\n", encoding="utf-8")
            return super().run(request)

    _age(vault)
    record = _run(config, Busy({"research": JobOutput(block="x", detail="d"),
                                "triage": JobOutput(block="y")}))

    assert next(e for e in record.entries if e.note == NOTE).outcome == "done"
    assert "the user was here" in other.read_text()
    # Tiro's commit carried its own note, not the user's other edit.
    assert BYSTANDER in Git(vault).dirty_paths()


class _MovesAndRewrites(FilesystemOps):
    """Obsidian's move: the note moves and every [[link]] to it is rewritten."""

    def move(self, src_rel: str, dst_rel: str) -> None:
        old, new = Path(src_rel).stem, dst_rel[:-3]
        (self.vault / dst_rel).parent.mkdir(parents=True, exist_ok=True)
        (self.vault / src_rel).rename(self.vault / dst_rel)
        for path in self.vault.rglob("*.md"):
            text = path.read_text(encoding="utf-8")
            if f"[[{old}]]" in text:
                path.write_text(text.replace(f"[[{old}]]", f"[[{new}]]"), encoding="utf-8")


def test_a_failed_move_is_undone_without_touching_what_tiro_did_not_write(config, vault: Path) -> None:
    """Something writes a note nobody declared during the move. It may be
    Obsidian, it may be the user saving: Tiro cannot tell, so it refuses the job,
    undoes what it can prove it did, and leaves that note as it found it."""
    src, dst = "Areas/target.md", "Tiro/target.md"
    (vault / src).write_text(
        f"---\ntiro: file\ntiro/filed-to: {dst}\n---\n\nthe note being filed\n", encoding="utf-8")
    pointer = vault / "Areas/pointer.md"
    pointer.write_text("points at [[target]]\n", encoding="utf-8")
    Git(vault)("add", "-A")
    Git(vault)("commit", "-qm", "a linked note")
    # And an edit of the user's that is not committed, in the note being filed.
    (vault / src).write_text((vault / src).read_text() + "an uncommitted line\n", encoding="utf-8")

    class SomeoneSaves(_MovesAndRewrites):
        def move(self, src_rel: str, dst_rel: str) -> None:
            super().move(src_rel, dst_rel)
            (self.vault / BYSTANDER).write_text("saved mid-move\n", encoding="utf-8")

    _age(vault)
    record = _run(config, ScriptedAgent({
        "file": JobOutput(keys={"tiro/filed-to": dst}, block="x"),
        "research": JobOutput(block="x"), "triage": JobOutput(block="y")}),
        ops=SomeoneSaves(vault))

    entry = next(e for e in record.entries if e.note == src)
    assert entry.outcome == "blocked"
    assert BYSTANDER in entry.detail
    # What Tiro did is undone: the note is home, with the user's line in it,
    # and the link Obsidian rewrote for the move points at it again.
    assert (vault / src).exists() and not (vault / dst).exists()
    assert "an uncommitted line" in (vault / src).read_text()
    assert pointer.read_text() == "points at [[target]]\n"
    # What Tiro did not do is not undone.
    assert (vault / BYSTANDER).read_text() == "saved mid-move\n"


# --- the undo log itself --------------------------------------------------


def test_undo_restores_the_bytes_before_the_job_not_head(vault: Path) -> None:
    """The user's uncommitted edit was there before Tiro wrote; putting the file
    back means putting that back too."""
    path = vault / NOTE
    path.write_text(path.read_text() + "uncommitted\n", encoding="utf-8")
    before = path.read_bytes()
    os.utime(path, (1000, 1000))

    undo = Undo(vault)
    undo.write(NOTE, "Tiro wrote this\n")
    assert undo.undo() == []

    assert path.read_bytes() == before
    assert path.stat().st_mtime == 1000  # as found, not "just typed"


def test_undo_leaves_a_file_changed_after_tiro_wrote_it(vault: Path) -> None:
    undo = Undo(vault)
    undo.write(NOTE, "Tiro wrote this\n")
    (vault / NOTE).write_text("then the user typed\n", encoding="utf-8")

    assert undo.undo() == [NOTE]
    assert (vault / NOTE).read_text() == "then the user typed\n"


def test_undo_removes_a_copy_tiro_made_and_nobody_touched(vault: Path) -> None:
    undo = Undo(vault)
    (vault / "Tiro").mkdir(exist_ok=True)
    undo.write("Tiro/copy.md", "a copy Tiro made\n")

    assert undo.undo() == []
    assert not (vault / "Tiro/copy.md").exists()


def test_undo_never_removes_a_file_the_user_has_touched(vault: Path) -> None:
    """Tiro created it, but the user has written in it since: it is theirs now."""
    undo = Undo(vault)
    (vault / "Tiro").mkdir(exist_ok=True)
    undo.write("Tiro/copy.md", "a copy Tiro made\n")
    (vault / "Tiro/copy.md").write_text("the user adopted it\n", encoding="utf-8")

    assert undo.undo() == ["Tiro/copy.md"]
    assert (vault / "Tiro/copy.md").read_text() == "the user adopted it\n"


def test_undo_does_not_touch_files_it_never_recorded(vault: Path) -> None:
    """An untracked note the user wrote five minutes ago is not in the record,
    so nothing can remove it."""
    theirs = vault / "00 Inbox/not-yet-committed.md"
    theirs.write_text("a thought the user had\n", encoding="utf-8")

    undo = Undo(vault)
    undo.write(NOTE, "Tiro wrote this\n")
    undo.undo()

    assert theirs.read_text() == "a thought the user had\n"


def test_a_change_that_never_finished_is_reported_not_guessed_at(vault: Path) -> None:
    """Tracked but never settled: Tiro knows what was there, not what it left."""
    undo = Undo(vault)
    undo.track(BYSTANDER)
    (vault / BYSTANDER).write_text("half a rewrite\n", encoding="utf-8")

    assert undo.undo() == [BYSTANDER]
    assert (vault / BYSTANDER).read_text() == "half a rewrite\n"
