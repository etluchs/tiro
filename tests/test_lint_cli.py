"""lint and the CLI.

lint is the first thing Tiro should be trusted with, because it proves the
vault can be read correctly before anything is written to it. So these tests
care about two things: that it finds what is actually wrong, and that it is
honest about how it knows.
"""

from __future__ import annotations

import os
from pathlib import Path

from tiro import cli, lint
from tiro.ops_fs import FilesystemOps


def _report(config):
    return lint.run(config, FilesystemOps(config.vault))


def test_lint_finds_the_broken_link_and_not_the_good_ones(config) -> None:
    report = _report(config)
    broken = {(f.note, f.detail) for f in report.broken_links}
    assert ("Teaching/HS26/prog-2.md", "[[missing-note]] (line 1)") in broken
    assert not any("Compute trends" in detail for _, detail in broken)


def test_lint_finds_orphans(config) -> None:
    assert "Areas/orphan.md" in _report(config).orphans


def test_tiros_own_links_do_not_make_a_note_stop_being_an_orphan(config, vault: Path) -> None:
    """The journal links every note Tiro touches. If those counted, nothing
    would be an orphan after the first run."""
    before = set(_report(config).orphans)

    (vault / "Tiro/Journal").mkdir(parents=True, exist_ok=True)
    (vault / "Tiro/Journal/2026-09-21.md").write_text(
        "- done [[Areas/orphan]]\n", encoding="utf-8")

    assert set(_report(config).orphans) == before


def test_lint_reports_protocol_problems(config, vault: Path) -> None:
    (vault / "00 Inbox/bad.md").write_text(
        "---\ntiro: reserch\ntiro/status: sideways\n---\n\nbody\n", encoding="utf-8")
    problems = {(f.kind, f.note) for f in _report(config).protocol_problems}
    assert ("unknown verb", "00 Inbox/bad.md") in problems
    assert ("invalid status", "00 Inbox/bad.md") in problems


def test_lint_notices_an_unclosed_block(config, vault: Path) -> None:
    (vault / "00 Inbox/half.md").write_text(
        "body\n\n<!-- tiro:begin job=research id=z -->\nhalf\n", encoding="utf-8")
    assert any(f.kind == "unclosed block" for f in _report(config).protocol_problems)


def test_lint_notices_a_note_stuck_working(config, vault: Path) -> None:
    (vault / "00 Inbox/stuck.md").write_text(
        "---\ntiro: research\ntiro/status: working\n---\n\nbody\n", encoding="utf-8")
    stuck = {f.note for f in _report(config).stuck}
    assert "00 Inbox/stuck.md" in stuck


def test_lint_notices_a_stale_inbox_note(config, vault: Path) -> None:
    old = vault / "00 Inbox/forgotten.md"
    old.write_text("written and never looked at again\n", encoding="utf-8")
    os.utime(old, (0, 0))
    assert any(f.note == "00 Inbox/forgotten.md" for f in _report(config).stale_inbox)


def test_lint_changes_nothing_but_its_own_report(config, vault: Path) -> None:
    from tiro.vcs import Git

    lint.write(config, _report(config))
    dirty = Git(vault).dirty_paths()
    assert all(p.startswith("Tiro/") or p.startswith(".tiro/") for p in dirty), dirty


def test_running_lint_twice_finds_the_same_thing(config, vault: Path) -> None:
    """A report that changes when nothing changed is a report nobody reads.

    The one thing that may differ between two runs of an unchanged vault is the
    trend column, which goes from "—" to the previous numbers. Everything else
    — the counts and every finding — must be identical.
    """
    first = _report(config)
    lint.write(config, first)
    body_first = _findings_only((vault / "Tiro/Health.md").read_text())

    second = _report(config)
    lint.write(config, second)
    body_second = _findings_only((vault / "Tiro/Health.md").read_text())

    assert first.counts == second.counts
    assert body_first == body_second


def test_the_report_says_when_its_numbers_are_an_approximation(config, vault: Path) -> None:
    lint.write(config, _report(config))
    text = (vault / "Tiro/Health.md").read_text()
    assert "approximation" in text
    assert "filesystem" in text


def test_the_report_shows_the_trend_against_the_last_run(config, vault: Path) -> None:
    lint.write(config, _report(config))
    (vault / "00 Inbox/another-break.md").write_text("[[also missing]]\n", encoding="utf-8")
    lint.write(config, _report(config))
    text = (vault / "Tiro/Health.md").read_text()
    assert "| broken links | 3 | 2 |" in text


def _findings_only(text: str) -> str:
    """The report minus the two parts that legitimately move between runs: the
    timestamp, and the trend column of the counts table."""
    return "\n".join(
        line for line in text.split("\n")
        if not line.startswith("Checked ") and not line.startswith("| ")
    )


# --- the CLI --------------------------------------------------------------


def _age(vault: Path) -> None:
    for path in vault.rglob("*.md"):
        os.utime(path, (0, 0))


def test_status_reports_the_queue_without_touching_anything(config, vault: Path, capsys) -> None:
    from tiro.vcs import Git

    _age(vault)
    code = cli.main(["--root", str(config.root), "--vault", str(vault), "--backend", "fs", "status"])
    out = capsys.readouterr().out

    assert code == 0
    assert "research" in out and "bitter-lesson" in out
    assert "queue" in out
    assert Git(vault).dirty_paths() == []


def test_status_says_when_obsidian_is_missing(config, vault: Path, capsys) -> None:
    cli.main(["--root", str(config.root), "--vault", str(vault), "--backend", "fs", "status"])
    out = capsys.readouterr().out
    assert "unavailable" in out
    assert "`file` is refused" in out


def test_once_dry_run_plans_and_stops(config, vault: Path, capsys) -> None:
    from tiro.vcs import Git

    _age(vault)
    code = cli.main(["--root", str(config.root), "--vault", str(vault),
                     "--backend", "fs", "once", "--dry-run"])
    out = capsys.readouterr().out

    assert code == 0
    assert "would run" in out
    assert Git(vault).dirty_paths() == []


def test_strip_removes_tiros_marks_and_leaves_the_prose(config, vault: Path, capsys) -> None:
    from tiro import protocol

    note = vault / "00 Inbox/bitter-lesson.md"
    text = protocol.set_key(note.read_text(), "tiro/status", "done")
    text = protocol.upsert_block(text, job="research", id="a1", body="Tiro's findings")
    note.write_text(text, encoding="utf-8")

    cli.main(["--root", str(config.root), "--vault", str(vault),
              "strip", "00 Inbox/bitter-lesson.md"])

    after = note.read_text()
    assert "Rich Sutton" in after
    assert "Tiro's findings" not in after
    assert "tiro/status" not in after
    assert "title: The Bitter Lesson" in after


def test_undo_reports_when_there_is_nothing_to_undo(config, vault: Path, capsys) -> None:
    code = cli.main(["--root", str(config.root), "--vault", str(vault), "undo", "nonexistent-run"])
    assert code == 1
    assert "no commits" in capsys.readouterr().out
