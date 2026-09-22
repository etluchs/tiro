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
    from dataclasses import replace

    from tiro.config import LintConfig

    old = vault / "00 Inbox/forgotten.md"
    old.write_text("written and never looked at again\n", encoding="utf-8")
    os.utime(old, (0, 0))
    # No inbox configured: nothing is stale, because there is nowhere to be
    # stale in. With one: the forgotten note is named.
    assert _report(config).stale_inbox == []
    with_inbox = replace(config, lint=LintConfig(inbox="00 Inbox/"))
    assert any(f.note == "00 Inbox/forgotten.md" for f in _report(with_inbox).stale_inbox)


def test_the_root_can_be_the_inbox_and_daily_notes_never_go_stale(config, vault: Path) -> None:
    from dataclasses import replace

    from tiro.config import LintConfig

    for name in ("loose.md", "2026-09-01.md"):
        p = vault / name
        p.write_text("something\n", encoding="utf-8")
        os.utime(p, (0, 0))
    old_in_folder = vault / "Areas/orphan.md"
    os.utime(old_in_folder, (0, 0))

    stale = {f.note for f in _report(replace(config, lint=LintConfig(inbox="/"))).stale_inbox}
    assert stale == {"loose.md"}


def test_lint_lists_leftovers_and_counts_empty_daily_notes_as_one_line(config, vault: Path) -> None:
    (vault / "Untitled.md").write_text("", encoding="utf-8")
    (vault / "Untitled 1.canvas").write_text("{}", encoding="utf-8")
    (vault / "stub.md").write_text("todo\n", encoding="utf-8")
    (vault / "2026-09-20.md").write_text("", encoding="utf-8")
    (vault / "2026-09-21.md").write_text("", encoding="utf-8")

    report = _report(config)
    kinds = {(f.kind, f.note) for f in report.leftovers}
    assert ("empty", "Untitled.md") in kinds
    assert ("never named", "Untitled.md") in kinds
    assert ("never named", "Untitled 1.canvas") in kinds
    assert ("almost empty", "stub.md") in kinds
    assert report.empty_daily == 2
    assert not any(f.note.startswith("2026-") for f in report.leftovers)

    lint.write(config, report)
    text = (vault / "Tiro/Health.md").read_text()
    assert "2 empty daily note(s)" in text
    assert "never named: [[Untitled 1.canvas]]" in text


def test_daily_notes_are_not_orphans(config, vault: Path) -> None:
    (vault / "2026-09-21.md").write_text("a day\n", encoding="utf-8")
    report = _report(config)
    assert "2026-09-21.md" not in report.orphans
    assert "Areas/orphan.md" in report.orphans


def test_lint_names_a_request_tiro_could_not_read(config, vault: Path) -> None:
    (vault / "00 Inbox/almost.md").write_text("a thought\n\n#tiro pls\n", encoding="utf-8")
    problems = {(f.kind, f.note) for f in _report(config).protocol_problems}
    assert ("unrecognised request", "00 Inbox/almost.md") in problems


def test_an_empty_trust_key_is_refused(vault: Path) -> None:
    from tiro.config import ConfigError, TrustMap

    (vault / ".tiro/trust.toml").write_text('default = "L1"\n"" = "L0"\n', encoding="utf-8")
    try:
        TrustMap.load(vault / ".tiro/trust.toml")
    except ConfigError as exc:
        assert '"/"' in str(exc)
    else:
        raise AssertionError("an empty key should be refused")

    # The root key it recommends must itself load — the draft adopt writes.
    (vault / ".tiro/trust.toml").write_text('default = "L3"\n"/" = "L4"\n"Roam/" = "L1"\n',
                                            encoding="utf-8")
    trust = TrustMap.load(vault / ".tiro/trust.toml")
    assert trust.level_for("loose.md") == "L4"
    assert trust.level_for("Roam/x.md") == "L1"
    assert trust.level_for("uzh/x.md") == "L3"


def test_plugin_directories_are_not_notes(config, vault: Path) -> None:
    from tiro.scan import iter_notes

    (vault / ".smart-env").mkdir()
    (vault / ".smart-env/cache.md").write_text("[[phantom]]\n", encoding="utf-8")
    assert not any(".smart-env" in str(p) for p in iter_notes(vault))
    assert not any(".smart-env" in f.note for f in _report(config).broken_links)


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


def test_a_skipped_note_is_reported_rather_than_called_nothing(config, vault: Path, capsys) -> None:
    """The run that prompted this said "nothing to do" while the journal
    recorded a skip. Silence about a note passed over is the failure rule 4 of
    the constitution names."""
    from tiro import runner
    from tiro.agent import JobOutput, ScriptedAgent

    # Every note is freshly written by the fixture, so the "user may be typing"
    # guard fires on all of them and no job runs.
    record = runner.once(config, agent=ScriptedAgent({"research": JobOutput(block="x")}),
                         ops=FilesystemOps(vault))
    assert record.entries == []
    assert record.skipped

    cli.cmd_once(_args(config, vault))
    out = capsys.readouterr().out
    assert "nothing to do" not in out
    assert "user may be typing" in out
    assert "bitter-lesson" in out

    journal = (vault / "Tiro/Journal" / f"{record.run_id[:10]}.md").read_text()
    assert "nothing to do" not in journal
    assert "user may be typing" in journal


def test_a_genuinely_quiet_run_still_says_so(config, vault: Path, capsys) -> None:
    _age(vault)
    from tiro import protocol, runner
    from tiro.agent import JobOutput, ScriptedAgent

    # Hash every tagged note so there is truly nothing to do.
    for path in vault.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        if protocol.verb(text):
            path.write_text(protocol.set_key(text, "tiro/hash", protocol.user_hash(text)),
                            encoding="utf-8")
    _age(vault)

    record = runner.once(config, agent=ScriptedAgent({}), ops=FilesystemOps(vault))
    assert record.entries == [] and record.skipped == []
    journal = (vault / "Tiro/Journal" / f"{record.run_id[:10]}.md").read_text()
    assert "nothing to do" in journal


def _args(config, vault: Path):
    import argparse

    return argparse.Namespace(root=str(config.root), vault=str(vault),
                              backend="fs", dry_run=False)
