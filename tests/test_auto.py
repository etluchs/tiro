"""Auto mode (ITERATION-3): notes looked after without being asked for.

The hostile half of this file is in the style of the gate's tests: every way an
auto move could go somewhere it should not, each refused. The rest is the
bounds that stop auto mode sweeping a vault it was never pointed at.
"""

from __future__ import annotations

import os
import time
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tiro import auto, cli, corrections, gate, lint, protocol, runner
from tiro.agent import JobOutput, JobRequest
from tiro.config import AutoConfig, TrustMap
from tiro.ops_fs import FilesystemOps
from tiro.vcs import Git

from conftest import REPO

SINCE = 1_000_000.0
LATER = SINCE + 1000  # written after auto mode was switched on
NOW = LATER + 3600  # and quiet for an hour since


class Moving(FilesystemOps):
    """The filesystem backend, able to move. The real move is Obsidian's."""

    can_move = True

    def move(self, src: str, dst: str) -> None:
        (self.vault / dst).parent.mkdir(parents=True, exist_ok=True)
        (self.vault / src).rename(self.vault / dst)


class ByNote:
    """A scripted agent that answers per note, so the fixture's own tagged
    notes can run alongside without getting the answer meant for this one."""

    name = "by-note"

    def __init__(self, answers: dict[str, JobOutput]) -> None:
        self.answers = answers
        self.calls: list[JobRequest] = []

    def run(self, request: JobRequest) -> JobOutput:
        self.calls.append(request)
        answer = self.answers.get(request.note_rel)
        return replace(answer, keys=dict(answer.keys)) if answer else JobOutput(block="y")


LIKE = {"like": ["Areas/orphan.md", "Areas/Compute trends.md"]}


def _obvious(dst: str, basis: dict = LIKE) -> JobOutput:
    return JobOutput(block="> [!abstract] Tiro · triage\n> A note.",
                     keys={"tiro/filed-to": dst}, obvious=True, basis=dict(basis),
                     detail="proposed Areas/")


@pytest.fixture
def on(config, vault: Path):
    """Auto mode on at SINCE, the root at L4 and Areas/ at L3, every fixture
    note older than that."""
    for path in vault.rglob("*.md"):
        os.utime(path, (0, 0))
    trust = TrustMap(default="L1", folders={
        "/": "L4", "Areas/": "L3", "Teaching/": "L3", "00 Inbox/": "L2",
        "Tiro/": "L4", "Private/": "L0"})
    cfg = replace(config, trust=trust, auto=AutoConfig())
    state = runner._load_state(cfg)
    auto.switch_on(state, now=SINCE)
    runner._save_state(cfg, state)
    return cfg


def _capture(vault: Path, rel: str, text: str = "Budget meeting notes for HS26.\n",
             mtime: float = LATER) -> Path:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def _scan(cfg, now: float = NOW):
    return auto.scan(cfg, runner._load_state(cfg), now=now)


def _run(cfg, agent, now: float = NOW):
    return runner.once(cfg, agent=agent, ops=Moving(cfg.vault), now=now)


def _entry(record, rel: str):
    return next(e for e in record.entries if e.note == rel)


# --- which notes ------------------------------------------------------------


def test_switching_it_on_looks_at_nothing_already_there(on) -> None:
    jobs, skipped = _scan(on)
    assert jobs == [] and skipped == []


def test_off_means_off(on, vault: Path) -> None:
    _capture(vault, "capture.md")
    state = runner._load_state(on)
    auto.switch_off(state)
    assert auto.scan(on, state, now=NOW) == ([], [])


def test_a_new_note_at_the_root_waits_until_it_has_settled(on, vault: Path) -> None:
    _capture(vault, "capture.md")
    jobs, skipped = _scan(on, now=LATER + 60)
    assert jobs == []
    assert "settling" in skipped[0].why

    jobs, _ = _scan(on)
    assert [(j.rel, j.verb, j.trigger) for j in jobs] == [("capture.md", "triage", "auto")]
    assert jobs[0].reason == "auto: new in the vault root"


def test_a_folder_not_listed_is_never_looked_at(on, vault: Path) -> None:
    _capture(vault, "Areas/new.md")
    _capture(vault, "Roam/2023-01-01 page.md")
    assert _scan(on) == ([], [])


def test_a_listed_folder_the_user_closed_is_named_not_written(on, vault: Path) -> None:
    cfg = replace(on, auto=AutoConfig(folders=("/", "Private/")))
    _capture(vault, "Private/diary.md")
    jobs, skipped = _scan(cfg)
    assert jobs == []
    assert "Private/" in skipped[0].why


def test_a_tagged_note_is_the_users_and_hold_means_hold(on, vault: Path) -> None:
    _capture(vault, "asked.md", "---\ntiro: research\n---\n\nwhy?\n")
    _capture(vault, "held.md", "---\ntiro: hold\n---\n\nmine\n")
    _capture(vault, "held-tag.md", "private thoughts #tiro/hold\n")
    assert _scan(on) == ([], [])
    from tiro.scan import scan
    assert [j.rel for j in scan(on, now=NOW)[0] if j.rel in ("held.md", "held-tag.md")] == []


def test_todays_daily_note_is_left_and_yesterdays_is_looked_at_once(on, vault: Path) -> None:
    now = datetime(2026, 10, 2, 8, 0).timestamp()
    _capture(vault, "2026-10-02.md", "today\n", mtime=now - 7200)
    _capture(vault, "2026-10-01.md", "yesterday, met Anna about the budget\n",
             mtime=now - 7200)
    jobs, _ = _scan(on, now=now)
    assert [j.rel for j in jobs] == ["2026-10-01.md"]
    assert jobs[0].reason == "auto: a daily note, the morning after"

    _run(on, ByNote({}), now=now)
    # Looked at once. Even an edit afterwards does not bring it back.
    path = vault / "2026-10-01.md"
    path.write_text(path.read_text() + "\nand one more thing\n")
    os.utime(path, (now, now))
    assert [j.rel for j in _scan(on, now=now + 86400)[0]] == ["2026-10-02.md"]


def test_a_note_is_looked_at_again_when_it_changes_but_once_a_day(on, vault: Path) -> None:
    path = _capture(vault, "capture.md")
    _run(on, ByNote({}))
    assert _scan(on)[0] == []  # its hash is recorded: nothing new

    path.write_text(path.read_text() + "\nMore.\n")
    os.utime(path, (LATER + 60, LATER + 60))
    jobs, skipped = _scan(on)
    assert jobs == [] and "once today" in skipped[0].why
    jobs, _ = _scan(on, now=NOW + 86400)
    assert jobs[0].reason == "auto: changed since Tiro last looked"


def test_with_obsidian_closed_a_note_that_might_move_waits(on, vault: Path) -> None:
    _capture(vault, "capture.md")
    record = runner.once(on, agent=ByNote({}), ops=FilesystemOps(vault), now=NOW)
    assert "capture.md" not in {e.note for e in record.entries}
    assert any("waiting for Obsidian" in s["why"] for s in record.skipped)
    assert _scan(on)[0]  # not marked as looked at: the next run tries again


def test_a_machine_failure_is_tried_again_next_run(on, vault: Path) -> None:
    from tiro.agent import AgentUnavailable

    class Down(ByNote):
        def run(self, request):
            raise AgentUnavailable("network down")

    _capture(vault, "capture.md")
    record = _run(on, Down({}))
    assert _entry(record, "capture.md").outcome == "blocked"
    assert [j.rel for j in _scan(on)[0]] == ["capture.md"]


def test_tiros_own_folder_is_never_an_auto_folder(on) -> None:
    assert "Tiro/" not in on.auto_folders()
    assert on.auto_folders() == ("/",)


def test_the_users_requests_go_first_and_auto_takes_what_is_left(on, vault: Path) -> None:
    for i in range(8):
        _capture(vault, f"capture-{i}.md", f"note {i}\n")
    record = _run(on, ByNote({}))
    unasked = [e for e in record.entries if e.trigger == "auto"]
    asked = [e for e in record.entries if e.trigger != "auto"]
    assert len(unasked) == on.auto.max_per_run
    assert record.entries[: len(asked)] == asked


# --- the obvious move --------------------------------------------------------


def test_an_obvious_capture_is_filed_said_and_journalled(on, vault: Path) -> None:
    _capture(vault, "capture.md")
    record = _run(on, ByNote({"capture.md": _obvious("Areas/Budget HS26.md")}))

    entry = _entry(record, "capture.md")
    assert entry.outcome == "done", entry.detail
    assert entry.trigger == "auto" and entry.moved_to == "Areas/Budget HS26.md"
    assert not (vault / "capture.md").exists()
    text = (vault / "Areas/Budget HS26.md").read_text()
    assert "Budget meeting notes" in text
    assert "Filed here from `capture.md`" in text and "unasked" in text
    assert f"tiro undo {record.run_id}" in text
    assert protocol.read_keys(text)["tiro/filed"] == "Areas/Budget HS26.md"

    git = Git(vault)
    log = git("log", "--format=%B", "-n", "3")
    assert "tiro(auto): capture" in log and "Tiro-Trigger: auto" in log
    journal = (vault / "Tiro/Journal" / f"{record.run_id[:10]}.md").read_text()
    unasked = journal.split("### Done unasked")[1].split("### The rest")[0]
    assert "[[Areas/Budget HS26]]" in unasked and "filed to" in unasked


def test_a_rule_is_a_basis_when_rules_md_has_it(on, vault: Path) -> None:
    (vault / ".tiro/rules.md").write_text("### R-014 — Budgets go in Areas\n")
    _capture(vault, "capture.md")
    record = _run(on, ByNote({"capture.md": _obvious("Areas/Budget.md", {"rule": "R-014"})}))
    assert _entry(record, "capture.md").moved_to == "Areas/Budget.md"
    assert "by R-014" in (vault / "Areas/Budget.md").read_text()


@pytest.mark.parametrize("dst, basis, why", [
    ("Areas/x.md", {"like": ["Areas/orphan.md"]}, "needs 2"),
    ("Areas/x.md", {"like": ["Areas/orphan.md", "Areas/invented.md"]}, "needs 2"),
    ("Areas/x.md", {"like": ["Areas/orphan.md", "Teaching/HS26/prog-2.md"]}, "needs 2"),
    ("Areas/x.md", {"rule": "R-099"}, "no rule R-099"),
    ("Areas/x.md", {}, "no basis"),
    ("Projects/x.md", {"like": ["Areas/orphan.md", "Areas/Compute trends.md"]},
     "Projects/ is L1"),
    ("Brand New/x.md", {"like": ["Areas/orphan.md", "Areas/Compute trends.md"]},
     "Brand New/ is L1"),
    ("Areas/orphan.md", {"like": ["Areas/orphan.md", "Areas/Compute trends.md"]},
     "already at"),
    ("../outside.md", {"like": ["Areas/orphan.md", "Areas/Compute trends.md"]},
     "outside the vault"),
])
def test_a_move_that_does_not_check_out_stays_a_proposal(on, vault: Path, dst, basis, why) -> None:
    _capture(vault, "capture.md")
    record = _run(on, ByNote({"capture.md": _obvious(dst, basis)}))
    entry = _entry(record, "capture.md")
    assert entry.outcome == "done" and entry.moved_to == ""
    text = (vault / "capture.md").read_text()
    assert "did not move it" in text and why in text, text
    assert protocol.read_keys(text)["tiro/filed-to"] == dst


def test_a_new_folder_is_the_users_call_even_at_l3(on, vault: Path) -> None:
    cfg = replace(on, trust=replace(on.trust, default="L3"))
    _capture(vault, "capture.md")
    _run(cfg, ByNote({"capture.md": _obvious("Budgets/x.md")}))
    assert "would be a new folder" in (vault / "capture.md").read_text()
    assert not (vault / "Budgets").exists()


def test_not_obvious_is_a_proposal(on, vault: Path) -> None:
    _capture(vault, "capture.md")
    out = replace(_obvious("Areas/x.md"), obvious=False)
    _run(on, ByNote({"capture.md": out}))
    text = (vault / "capture.md").read_text()
    assert "not sure enough" in text and "add `tiro: file`" in text


def test_a_folder_below_l4_gets_proposals_only(on, vault: Path) -> None:
    cfg = replace(on, auto=AutoConfig(folders=("/", "00 Inbox/")))
    _capture(vault, "00 Inbox/capture.md")
    _run(cfg, ByNote({"00 Inbox/capture.md": _obvious("Areas/x.md")}))
    text = (vault / "00 Inbox/capture.md").read_text()
    assert "only out of L4 folders" in text


def test_a_daily_note_never_moves(on, vault: Path) -> None:
    now = datetime(2026, 10, 2, 8, 0).timestamp()
    _capture(vault, "2026-10-01.md", "met Anna\n", mtime=now - 7200)
    _run(on, ByNote({"2026-10-01.md": _obvious("Areas/x.md")}), now=now)
    text = (vault / "2026-10-01.md").read_text()
    assert "tiro/filed-to" not in protocol.read_keys(text)
    assert not (vault / "Areas/x.md").exists()


def test_a_note_the_user_corrected_is_never_moved_again(on, vault: Path) -> None:
    path = _capture(vault, "capture.md", "---\ntiro/id: abc123\n---\n\nbudget\n")
    corrections.log(on, [corrections.Correction(
        "moved-after-filing", "capture.md", "Areas/x.md", "capture.md", "abc123")])
    os.utime(path, (LATER, LATER))
    _run(on, ByNote({"capture.md": _obvious("Areas/x.md")}))
    assert "corrected Tiro on this note" in path.read_text()


def test_tiro_files_a_note_once(on, vault: Path) -> None:
    cfg = replace(on, auto=AutoConfig(folders=("/", "Areas/")),
                  trust=replace(on.trust, folders={**on.trust.folders, "Areas/": "L4",
                                                   "Teaching/HS26/": "L3"}))
    _capture(vault, "capture.md")
    _run(cfg, ByNote({"capture.md": _obvious("Areas/x.md")}))
    moved = vault / "Areas/x.md"
    moved.write_text(moved.read_text() + "\nlater thoughts\n")
    os.utime(moved, (LATER, LATER))
    record = _run(cfg, ByNote({"Areas/x.md": _obvious(
        "Teaching/HS26/x.md", {"like": ["Teaching/HS26/prog-2.md", "Teaching/HS26/y.md"]})}),
        now=NOW + 86400)
    assert _entry(record, "Areas/x.md").moved_to == ""
    assert "filed this note once already" in moved.read_text()


# --- undo ---------------------------------------------------------------------


def test_undo_puts_a_never_committed_capture_back_and_it_stays_put(on, vault: Path) -> None:
    before = _capture(vault, "capture.md").read_bytes()
    record = _run(on, ByNote({"capture.md": _obvious("Areas/x.md")}))
    assert (vault / "Areas/x.md").exists()

    assert cli.main(["--root", str(REPO), "--vault", str(vault),
                     "undo", record.run_id]) == 0
    assert (vault / "capture.md").read_bytes() == before
    assert not (vault / "Areas/x.md").exists()

    # The same note is not looked at again...
    os.utime(vault / "capture.md", (LATER, LATER))
    jobs, skipped = _scan(on, now=NOW + 86400)
    assert jobs == [] and "you undid" in skipped[0].why
    # ...and once the user edits it, it is proposed for but never moved.
    (vault / "capture.md").write_text("Budget meeting notes, revised.\n")
    os.utime(vault / "capture.md", (LATER, LATER))
    _run(on, ByNote({"capture.md": _obvious("Areas/x.md")}), now=NOW + 86400)
    assert "you undid Tiro's work" in (vault / "capture.md").read_text()


def test_undo_of_a_request_keeps_the_users_uncommitted_words(on, vault: Path) -> None:
    """The job commit used to carry the user's uncommitted edit, so reverting
    it reverted them too."""
    path = vault / "00 Inbox/bitter-lesson.md"
    path.write_text(path.read_text() + "\nA thought I have not committed.\n")
    os.utime(path, (0, 0))
    record = _run(on, ByNote({}))
    cli.main(["--root", str(REPO), "--vault", str(vault), "undo", record.run_id])
    assert "A thought I have not committed." in path.read_text()
    assert "tiro:begin" not in path.read_text()


# --- the gate, directly ------------------------------------------------------


def _moved_by_hand(cfg, vault: Path, src: str, dst: str):
    git = Git(vault)
    _capture(vault, src, "a note\n", mtime=0)
    git("add", "-A")
    git("commit", "-qm", "setup")
    before = gate.snapshot(cfg, git, FilesystemOps(vault), src)
    (vault / dst).parent.mkdir(parents=True, exist_ok=True)
    (vault / src).rename(vault / dst)
    return git, before


def test_the_gate_refuses_an_auto_move_out_of_a_folder_below_l4(on, vault: Path) -> None:
    git, before = _moved_by_hand(on, vault, "Areas/a.md", "Teaching/a.md")
    result = gate.check(on, git, FilesystemOps(vault), verb="triage", note_rel="Areas/a.md",
                        declared=["Areas/a.md", "Teaching/a.md"], before=before,
                        moved=("Areas/a.md", "Teaching/a.md"), trigger="auto")
    assert not result.ok
    assert any("unasked needs L4" in f for f in result.failures)


def test_the_gate_refuses_an_auto_move_that_made_a_folder(on, vault: Path) -> None:
    git, before = _moved_by_hand(on, vault, "a.md", "Areas/new/a.md")
    result = gate.check(on, git, FilesystemOps(vault), verb="triage", note_rel="a.md",
                        declared=["a.md", "Areas/new/a.md"], before=before,
                        moved=("a.md", "Areas/new/a.md"), trigger="auto", new_folder=True)
    assert any("new folder" in f for f in result.failures)


def test_a_requested_triage_still_may_not_move(on, vault: Path) -> None:
    git, before = _moved_by_hand(on, vault, "a.md", "Areas/a.md")
    result = gate.check(on, git, FilesystemOps(vault), verb="triage", note_rel="a.md",
                        declared=["a.md", "Areas/a.md"], before=before,
                        moved=("a.md", "Areas/a.md"))
    assert any("only `file` may do that" in f for f in result.failures)


# --- what the model is told, and lint ---------------------------------------


def test_the_model_is_told_nobody_asked(on, vault: Path) -> None:
    _capture(vault, "capture.md")
    agent = ByNote({})
    _run(on, agent)
    request = next(r for r in agent.calls if r.note_rel == "capture.md")
    assert request.verb == "triage"
    assert "Nobody asked for this job" in request.skill


def test_obvious_must_be_literally_true() -> None:
    assert JobOutput.from_json({"obvious": "yes"}).obvious is False
    assert JobOutput.from_json({"obvious": True, "basis": "R-1"}).basis == {}


def test_lint_reads_hold_and_auto_state_as_fine(on, vault: Path) -> None:
    _capture(vault, "held.md", "---\ntiro: hold\n---\n\nmine\n")
    _capture(vault, "capture.md")
    _run(on, ByNote({}))
    report = lint.run(on, FilesystemOps(vault))
    flagged = {f.note for f in report.protocol_problems}
    assert not flagged & {"held.md", "capture.md"}, report.protocol_problems
