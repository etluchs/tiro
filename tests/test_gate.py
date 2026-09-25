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


def test_an_explicit_accept_lets_a_note_leave_an_ordinary_folder(scene, vault: Path) -> None:
    """`tiro: file` is the accept the constitution asks for. Areas/ is L1, and
    that is enough for the source: the user said so on the note itself."""
    src = "Areas/orphan.md"
    (vault / src).rename(vault / "Tiro/orphan.md")
    result = _check(scene, verb="file", declared=[src, "Tiro/orphan.md"],
                    moved=(src, "Tiro/orphan.md"))
    assert result.ok, result.failures


def test_nothing_leaves_a_folder_the_user_closed(scene, vault: Path) -> None:
    """L0 is "Tiro does not touch this", and a tag inside it changes nothing."""
    config, git, ops, _ = scene
    src = vault / "Private/diary.md"
    src.parent.mkdir()
    src.write_text("---\ntiro: file\ntiro/filed-to: Tiro/diary.md\n---\n\nmine\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "a private note")
    before = gate.snapshot(config, git, ops, "Private/diary.md")
    src.rename(vault / "Tiro/diary.md")
    result = gate.check(config, git, ops, verb="file", note_rel="Private/diary.md",
                        declared=["Private/diary.md", "Tiro/diary.md"], before=before,
                        moved=("Private/diary.md", "Tiro/diary.md"))
    assert not result.ok
    assert any("cannot move a note out of" in f for f in result.failures)


def test_the_tag_on_a_note_is_consent_enough_to_write_on_it(scene, vault: Path) -> None:
    """Areas/ is L1, research needs L2 — and the user tagged the note. The
    tag raises the note, and only the note, to L2."""
    config, git, ops, _ = scene
    note = "Areas/Compute trends.md"
    path = vault / note
    path.write_text(path.read_text().replace("---\naliases", "---\ntiro: research\naliases", 1),
                    encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "tagged")
    before = gate.snapshot(config, git, ops, note)
    path.write_text(path.read_text() + "\n<!-- tiro:begin job=research id=a1 -->\nx\n<!-- tiro:end id=a1 -->\n",
                    encoding="utf-8")
    result = gate.check(config, git, ops, verb="research", note_rel=note,
                        declared=[note], before=before)
    assert result.ok, result.failures

    # A tag in an L0 folder is a note, not a request.
    assert gate.note_trust(config, "Private/diary.md") == "L0"
    assert gate.note_trust(config, "Areas/x.md") == "L2"
    assert gate.note_trust(config, "00 Inbox/x.md") == "L4"


def test_every_failure_is_reported_not_just_the_first(scene, vault: Path) -> None:
    (vault / "Areas/orphan.md").unlink()
    (vault / "Areas/Compute trends.md").write_text("rewritten\n", encoding="utf-8")
    result = _check(scene)
    assert len(result.failures) >= 2
