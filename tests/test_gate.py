"""The gate, against the diffs it exists to stop.

The agent is read-only on the vault (see ``agent.py``), so none of these can be
produced by a model today. They are here because the gate also guards against
the runner's own bugs, against a misbehaving backend, and against whatever gets
write access in a later iteration. A safety check that is only tested by the
thing it is meant to catch is not tested at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tiro import gate
from tiro.ops_fs import FilesystemOps
from tiro.vcs import Git

NOTE = "00 Inbox/bitter-lesson.md"


@pytest.fixture
def scene(config, vault: Path):
    git = Git(vault)
    ops = FilesystemOps(vault)
    before = gate.snapshot(config, git, ops, NOTE)
    return config, git, ops, before


def _check(scene, *, verb="research", declared=(NOTE,), moved=None):
    config, git, ops, before = scene
    return gate.check(
        config, git, ops,
        verb=verb, note_rel=NOTE, declared=list(declared),
        before=before, moved=moved,
    )


def test_an_honest_job_passes(scene, vault: Path) -> None:
    path = vault / NOTE
    path.write_text(
        path.read_text() + "\n<!-- tiro:begin job=research id=a1 -->\nfindings\n<!-- tiro:end id=a1 -->\n",
        encoding="utf-8",
    )
    assert _check(scene).ok


def test_deleting_a_note_is_refused(scene, vault: Path) -> None:
    (vault / "Areas/orphan.md").unlink()
    result = _check(scene, declared=[NOTE, "Areas/orphan.md"])
    assert not result.ok
    assert any("never deletes" in f for f in result.failures)


def test_editing_a_path_the_job_did_not_declare_is_refused(scene, vault: Path) -> None:
    (vault / "Areas/Compute trends.md").write_text("rewritten\n", encoding="utf-8")
    result = _check(scene)
    assert not result.ok
    assert any("undeclared path" in f for f in result.failures)


def test_writing_outside_the_jobs_trust_level_is_refused(scene, vault: Path) -> None:
    # Areas/ is L1 by the fixture's trust.toml; research needs L2.
    target = "Areas/Compute trends.md"
    (vault / target).write_text("touched\n", encoding="utf-8")
    result = _check(scene, declared=[NOTE, target])
    assert not result.ok
    assert any("needs L2" in f for f in result.failures)


def test_breaking_an_inbound_link_is_refused(scene, vault: Path) -> None:
    # Teaching/HS26/prog-2.md embeds [[Compute trends]]; renaming it breaks that.
    (vault / "Areas/Compute trends.md").rename(vault / "Areas/Renamed.md")
    result = _check(scene, declared=[NOTE, "Areas/Compute trends.md", "Areas/Renamed.md"])
    assert not result.ok
    assert any("broke a link" in f for f in result.failures)


def test_corrupting_the_notes_status_is_refused(scene, vault: Path) -> None:
    path = vault / NOTE
    path.write_text(path.read_text().replace("tiro: research", "tiro: research\ntiro/status: sideways"),
                    encoding="utf-8")
    result = _check(scene)
    assert not result.ok
    assert any("invalid tiro/status" in f for f in result.failures)


def test_an_unclosed_block_is_refused(scene, vault: Path) -> None:
    path = vault / NOTE
    path.write_text(path.read_text() + "\n<!-- tiro:begin job=research id=a1 -->\nhalf\n",
                    encoding="utf-8")
    result = _check(scene)
    assert not result.ok
    assert any("unclosed" in f for f in result.failures)


def test_a_move_is_allowed_for_file_and_only_for_file(scene, vault: Path) -> None:
    # The fixture puts the inbox at L4 (things are meant to leave it) and Tiro/
    # at L4, which is also >= the L3 a destination needs.
    src, dst = vault / NOTE, vault / "Tiro/bitter-lesson.md"
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    declared = [NOTE, "Tiro/bitter-lesson.md"]
    pair = (NOTE, "Tiro/bitter-lesson.md")

    as_research = _check(scene, verb="research", declared=declared, moved=pair)
    assert not as_research.ok

    as_file = _check(scene, verb="file", declared=declared, moved=pair)
    assert as_file.ok, as_file.failures


def test_filing_into_a_folder_the_user_has_not_opened_up_is_refused(scene, vault: Path) -> None:
    # Areas/ is L1: the user has not said Tiro may put notes there.
    src, dst = vault / NOTE, vault / "Areas/bitter-lesson.md"
    src.rename(dst)
    result = _check(scene, verb="file", declared=[NOTE, "Areas/bitter-lesson.md"],
                    moved=(NOTE, "Areas/bitter-lesson.md"))
    assert not result.ok
    assert any("cannot file into" in f and "L3" in f for f in result.failures)


def test_taking_a_note_out_of_an_established_area_is_refused(scene, vault: Path) -> None:
    src = "Areas/orphan.md"
    (vault / src).rename(vault / "Tiro/orphan.md")
    result = _check(scene, verb="file", declared=[src, "Tiro/orphan.md"],
                    moved=(src, "Tiro/orphan.md"))
    assert not result.ok
    assert any("cannot move a note out of" in f for f in result.failures)


def test_every_failure_is_reported_not_just_the_first(scene, vault: Path) -> None:
    (vault / "Areas/orphan.md").unlink()
    (vault / "Areas/Compute trends.md").write_text("rewritten\n", encoding="utf-8")
    result = _check(scene)
    assert len(result.failures) >= 2


def test_rollback_restores_tracked_files(config, vault: Path) -> None:
    git = Git(vault)
    (vault / NOTE).write_text("clobbered\n", encoding="utf-8")

    gate.rollback(git, [NOTE])

    assert "Rich Sutton" in (vault / NOTE).read_text()
    assert git.dirty_paths() == []


def test_rollback_leaves_an_uncommitted_note_of_the_users_alone(config, vault: Path) -> None:
    """The first rule in the constitution outranks tidying up after ourselves.

    An untracked note might be one the user wrote five minutes ago and has not
    committed. Rollback cannot tell, so it does not touch it."""
    git = Git(vault)
    theirs = vault / "00 Inbox/not-yet-committed.md"
    theirs.write_text("a thought the user had\n", encoding="utf-8")

    gate.rollback(git, [NOTE, "00 Inbox/not-yet-committed.md"])

    assert theirs.exists()
    assert theirs.read_text() == "a thought the user had\n"


def test_rollback_removes_only_what_tiro_created(config, vault: Path) -> None:
    git = Git(vault)
    ours = vault / "Tiro/copy-we-made.md"
    ours.parent.mkdir(parents=True, exist_ok=True)
    ours.write_text("a half-finished move\n", encoding="utf-8")
    theirs = vault / "00 Inbox/not-yet-committed.md"
    theirs.write_text("theirs\n", encoding="utf-8")

    gate.rollback(git, [NOTE], created=["Tiro/copy-we-made.md"])

    assert not ours.exists()
    assert theirs.exists()
