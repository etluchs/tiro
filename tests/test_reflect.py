"""reflect, accept, reject (ITERATION-2 M2 and M3).

The plan's own criteria, one test each where it can be: three corrections in
one direction make one proposal naming them; two do not; opposing directions
make none; an accepted rule lands in rules.md with provenance and reaches the
next triage; a rejected one is never proposed again; and nothing but an
accept ever changes rules.md.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tiro import cli, corrections, reflect, runner
from tiro.agent import JobOutput, ScriptedAgent
from tiro.corrections import Correction
from tiro.ops_fs import FilesystemOps
from tiro.vcs import Git

TODAY = "2026-09-24"


def _correct(config, *moves: tuple[str, str], kind: str = "overrode-proposal") -> None:
    """Log one correction per (Tiro's choice, the user's choice)."""
    corrections.log(config, [
        Correction(kind, now, was, now, note_id=f"id-{i}-{now}", when=f"2026-09-{10 + i:02d}T09:00:00")
        for i, (was, now) in enumerate(moves)
    ])


THREE = [("Areas/a.md", "Teaching/a.md"), ("Areas/b.md", "Teaching/b.md"),
         ("Areas/c.md", "Teaching/c.md")]


def test_three_corrections_one_way_make_one_proposal_naming_them(config) -> None:
    _correct(config, *THREE)
    report = reflect.reflect(config, today=TODAY)

    assert [p.id for p in report.new] == ["R-001"]
    p = report.new[0]
    assert (p.src, p.dst, p.status, p.threshold) == ("Areas", "Teaching", "pending", 3)
    assert [c["now"] for c in p.corrections] == ["Teaching/a.md", "Teaching/b.md", "Teaching/c.md"]

    board = (config.notes_dir / "Proposals.md").read_text()
    assert "R-001" in board and "`Areas/`" in board and "`Teaching/`" in board
    assert "tiro accept R-001" in board
    for when in ("2026-09-10", "2026-09-11", "2026-09-12"):
        assert when in board  # each correction is named by date and note


def test_two_are_not_enough(config) -> None:
    _correct(config, *THREE[:2])
    report = reflect.reflect(config, today=TODAY)
    assert report.new == []
    assert report.below_threshold and report.below_threshold[0][1] == 2
    assert "2 so far, 3 needed" in (config.notes_dir / "Proposals.md").read_text()


def test_opposing_corrections_make_no_proposal(config) -> None:
    _correct(config, *THREE, ("Teaching/z.md", "Areas/z.md"))
    report = reflect.reflect(config, today=TODAY)
    assert report.new == []
    assert report.held_back
    assert "moved notes both ways" in (config.notes_dir / "Proposals.md").read_text()


def test_one_note_corrected_twice_counts_once(config) -> None:
    """A note the user overrode and then moved again is one piece of evidence,
    not two. Three corrections from two notes is not three notes."""
    corrections.log(config, [
        Correction("overrode-proposal", "Teaching/a.md", "Areas/a.md", "Teaching/a.md",
                   note_id="same", when="2026-09-10T09:00:00"),
        Correction("moved-after-filing", "Teaching/a2.md", "Areas/a2.md", "Teaching/a2.md",
                   note_id="same", when="2026-09-11T09:00:00"),
        Correction("overrode-proposal", "Teaching/b.md", "Areas/b.md", "Teaching/b.md",
                   note_id="other", when="2026-09-12T09:00:00"),
    ])
    report = reflect.reflect(config, today=TODAY)
    assert report.new == []
    assert report.below_threshold[0][1] == 2


def test_a_rename_inside_one_folder_is_not_a_direction(config) -> None:
    _correct(config, ("Areas/a.md", "Areas/renamed-a.md"), ("Areas/b.md", "Areas/b2.md"),
             ("Areas/c.md", "Areas/c2.md"))
    assert reflect.reflect(config, today=TODAY).new == []


def test_deleted_blocks_are_counted_and_make_no_rule(config) -> None:
    corrections.log(config, [Correction("rejected-block", f"Areas/{n}.md", f"id{n}", "deleted",
                                        note_id=f"id{n}") for n in "abcd"])
    report = reflect.reflect(config, today=TODAY)
    assert report.new == [] and report.rejected_blocks == 4
    assert "makes no rule" in (config.notes_dir / "Proposals.md").read_text()


def test_moves_after_filing_count_as_corrections_too(config) -> None:
    _correct(config, *THREE, kind="moved-after-filing")
    assert len(reflect.reflect(config, today=TODAY).new) == 1


def test_root_is_named_as_the_root(config) -> None:
    _correct(config, ("a.md", "Teaching/a.md"), ("b.md", "Teaching/b.md"), ("c.md", "Teaching/c.md"))
    p = reflect.reflect(config, today=TODAY).new[0]
    assert p.src == "" and "the vault root" in p.title


def test_numbering_continues_after_the_rules_already_written(config) -> None:
    (config.tiro_dir / "rules.md").write_text("### R-007 — something\nText.\n", encoding="utf-8")
    _correct(config, *THREE)
    assert reflect.reflect(config, today=TODAY).new[0].id == "R-008"


def test_a_rule_mentioning_the_same_folder_is_named(config) -> None:
    (config.tiro_dir / "rules.md").write_text(
        "### R-002 — Didactics\nResearch about teaching goes to `Areas/`.\n\n"
        "### R-003 — Unrelated\nDaily notes stay put.\n", encoding="utf-8")
    _correct(config, *THREE)
    p = reflect.reflect(config, today=TODAY).new[0]
    assert p.conflicts == ["R-002"]
    assert "Read with R-002" in (config.notes_dir / "Proposals.md").read_text()


# --- accept ---------------------------------------------------------------


def test_accept_appends_the_rule_with_its_provenance(config) -> None:
    (config.tiro_dir / "rules.md").write_text("# Filing rules\n\n### R-001 — mine\nKeep.\n",
                                              encoding="utf-8")
    _correct(config, *THREE)
    p = reflect.reflect(config, today=TODAY).new[0]

    reflect.accept(config, p.id, today="2026-09-25")

    rules = (config.tiro_dir / "rules.md").read_text()
    assert "### R-001 — mine\nKeep." in rules  # the user's own rule untouched
    assert f"### {p.id} — Notes Tiro proposed for `Areas/` go in `Teaching/`" in rules
    assert "Since 2026-09-25. Proposed by Tiro after 3 corrections" in rules
    assert "`Teaching/a.md`" in rules
    assert "Nothing waiting on you." in (config.notes_dir / "Proposals.md").read_text()


def test_an_accepted_rule_reaches_the_next_triage(config, vault: Path) -> None:
    _correct(config, *THREE)
    p = reflect.reflect(config, today=TODAY).new[0]
    reflect.accept(config, p.id, today=TODAY)

    for path in vault.rglob("*.md"):
        os.utime(path, (0, 0))
    agent = ScriptedAgent({"triage": JobOutput(block="y"), "research": JobOutput(block="x")})
    runner.once(config, agent=agent, ops=FilesystemOps(vault))

    triage = next(c for c in agent.calls if c.verb == "triage")
    assert ".tiro/rules.md` — read it first" in triage.skill
    assert p.title in (vault / ".tiro/rules.md").read_text()


def test_accept_and_reject_only_what_is_pending(config) -> None:
    _correct(config, *THREE)
    p = reflect.reflect(config, today=TODAY).new[0]
    reflect.accept(config, p.id, today=TODAY)
    with pytest.raises(ValueError, match="already accepted"):
        reflect.accept(config, p.id, today=TODAY)
    with pytest.raises(KeyError):
        reflect.accept(config, "R-999", today=TODAY)


# --- reject ---------------------------------------------------------------


def test_a_rejected_proposal_is_never_made_again(config) -> None:
    _correct(config, *THREE)
    p = reflect.reflect(config, today=TODAY).new[0]
    reflect.reject(config, p.id, "Areas/ is right for research about teaching", today=TODAY)

    # More evidence in the same direction arrives.
    _correct(config, ("Areas/d.md", "Teaching/d.md"), ("Areas/e.md", "Teaching/e.md"),
             ("Areas/f.md", "Teaching/f.md"), ("Areas/g.md", "Teaching/g.md"))
    assert reflect.reflect(config, today="2026-10-01").new == []

    board = (config.notes_dir / "Proposals.md").read_text()
    assert "rejected" in board and "research about teaching" in board
    assert not (config.tiro_dir / "rules.md").exists()


def test_reject_needs_a_reason(config) -> None:
    _correct(config, *THREE)
    p = reflect.reflect(config, today=TODAY).new[0]
    with pytest.raises(ValueError, match="say why"):
        reflect.reject(config, p.id, "  ", today=TODAY)


# --- cadence, and the commands --------------------------------------------


def test_reflect_is_weekly() -> None:
    from dataclasses import dataclass

    @dataclass
    class C:
        reflect: object

    from tiro.config import ReflectConfig

    c = C(ReflectConfig(every_days=7))
    assert reflect.due(c, {}, TODAY)
    state: dict = {}
    reflect.mark_done(state, "2026-09-20")
    assert not reflect.due(c, state, "2026-09-26")
    assert reflect.due(c, state, "2026-09-27")


def test_a_run_proposes_when_reflect_is_due_and_says_so(config, vault: Path) -> None:
    _correct(config, *THREE)
    for path in vault.rglob("*.md"):
        os.utime(path, (0, 0))
    record = runner.once(config, agent=ScriptedAgent(
        {"triage": JobOutput(block="y"), "research": JobOutput(block="x")}),
        ops=FilesystemOps(vault))

    assert any("proposes **R-001**" in n for n in record.notes)
    journal = (vault / "Tiro/Journal" / f"{record.run_id[:10]}.md").read_text()
    assert "tiro accept R-001" in journal

    second = runner.once(config, agent=ScriptedAgent({}), ops=FilesystemOps(vault))
    assert not any("proposes" in n for n in second.notes)  # not again this week


def test_only_an_accept_commit_changes_rules_md(config, vault: Path, capsys) -> None:
    """ITERATION-2 acceptance criterion 2, checked the way the week will check
    it: over the history."""
    _correct(config, *THREE)
    args = ["--root", str(config.root), "--vault", str(vault)]
    assert cli.main([*args, "reflect"]) == 0
    assert cli.main([*args, "accept", "R-001"]) == 0

    git = Git(vault)
    touching = git("log", "--format=%s", "--", ".tiro/rules.md").splitlines()
    assert touching == ["tiro accept R-001: Notes Tiro proposed for `Areas/` go in `Teaching/`"]
    assert "accepted R-001" in capsys.readouterr().out


def test_reject_from_the_command_line(config, vault: Path, capsys) -> None:
    _correct(config, *THREE)
    args = ["--root", str(config.root), "--vault", str(vault)]
    cli.main([*args, "reflect"])
    assert cli.main([*args, "reject", "R-001", "not a pattern, a coincidence"]) == 0
    assert cli.main([*args, "accept", "R-001"]) == 1
    out = capsys.readouterr().out
    assert "will not be proposed again" in out and "already rejected" in out
