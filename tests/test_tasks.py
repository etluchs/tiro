"""Moving open tasks to the note they belong in (docs/TASK-MOVES.md).

Triage proposes a destination per task; the user ticks a box per move; the
runner moves the task, verbatim, and checks that nothing else changed. The
first half is the text mechanics; the second is the loop, end to end, with a
scripted agent, real notes and real git, against the spec's criteria.
"""

from __future__ import annotations

import os
import re
from dataclasses import replace
from pathlib import Path

import pytest

from tiro import auto, cli, protocol, runner, tasks
from tiro.agent import JobOutput, ScriptedAgent
from tiro.config import AutoConfig, TasksConfig, TrustMap
from tiro.ops_fs import FilesystemOps
from tiro.vcs import Git

DAY = "2026-10-17.md"
HUB = "Projects/JupyterHub next.md"
OTHER = "Projects/Teaching.md"

DAILY = """---
tiro: triage
---
# Friday

- [ ] Fix the kernel spawner #jupyterhub 📅 2026-10-20
    - check the logs first
    - [ ] then file the bug
- [ ] Call the dentist
- [x] Merge the docs PR ✅ 2026-09-30
- [ ] Write the upgrade note ⏫

Some prose about the day.
"""

HUB_TEXT = """- [ ] Upgrade to 5.x
- [x] Pin the image ✅ 2026-09-30

## Notes

The hub runs on [[Compute trends]].
"""

ANSWERS = [
    {"n": 1, "to": HUB, "why": "the hub's backlog"},
    {"n": 2, "to": "", "why": "a personal errand"},
    {"n": 3, "to": "JupyterHub next", "why": "the hub's backlog"},
]


def _write(vault: Path, rel: str, text: str) -> None:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _age(vault: Path) -> None:
    for path in vault.rglob("*.md"):
        os.utime(path, (0.0, 0.0))


@pytest.fixture
def ready(config, vault: Path):
    _write(vault, DAY, DAILY)
    _write(vault, HUB, HUB_TEXT)
    _write(vault, OTHER, "- [ ] Grade the exams\n")
    _write(vault, "Roam/Old tasks.md", "- [ ] something from 2023\n")
    git = Git(vault)
    git("add", "-A")
    git("commit", "-qm", "task fixture")
    _age(vault)
    return replace(
        config,
        trust=TrustMap(default="L1", folders={"/": "L2", "Projects/": "L3", "Roam/": "L3",
                                              "Private/": "L0"}),
        tasks=TasksConfig(never_into=("Roam/",)),
    )


def _triage(config, answers=ANSWERS, note=DAY):
    agent = ScriptedAgent({"triage": JobOutput(
        block="> [!abstract] Tiro · triage · 2026-10-18\n> A day.", tasks=list(answers))})
    return runner.once(config, agent=agent, ops=FilesystemOps(config.vault), only={note})


def _carry(config, note=DAY):
    """A run after the user ticked: no model is called, or this would fail."""
    return runner.once(config, agent=ScriptedAgent({}), ops=FilesystemOps(config.vault),
                       only={note})


def _tick(vault: Path, words: str, rel: str = DAY) -> None:
    path = vault / rel
    lines = path.read_text(encoding="utf-8").split("\n")
    hits = [i for i, l in enumerate(lines) if l.startswith("- [ ] Move to") and words in l]
    assert len(hits) == 1, (words, lines)
    lines[hits[0]] = "- [x]" + lines[hits[0]][5:]
    path.write_text("\n".join(lines), encoding="utf-8")
    _age(vault)


def _block(vault: Path, rel: str = DAY) -> str:
    block = tasks.block_of((vault / rel).read_text(encoding="utf-8"))
    assert block is not None
    return block.body


# --- reading tasks -------------------------------------------------------


def test_a_task_carries_everything_indented_under_it() -> None:
    found = tasks.tasks(DAILY)
    assert [t.text for t in found] == [
        "Fix the kernel spawner #jupyterhub", "Call the dentist",
        "Merge the docs PR", "Write the upgrade note"]
    first = found[0]
    assert first.lines == ("- [ ] Fix the kernel spawner #jupyterhub 📅 2026-10-20",
                           "    - check the logs first", "    - [ ] then file the bug")
    assert [t.open for t in found] == [True, True, False, True]
    assert not any(t.problem for t in found)


def test_tasks_tiro_cannot_move_are_named_with_the_reason() -> None:
    text = ("- Meeting with Anna\n  - [ ] send her the slides\n"
            "> [!note]\n> - [ ] a task in a callout\n"
            "1. [ ] a numbered task\n"
            "- [ ] linked from elsewhere ^abc123\n"
            "- [ ] runs on\nwithout a blank line\n")
    why = {t.text: t.problem for t in tasks.tasks(text)}
    assert "item above" in why["send her the slides"]
    assert "callout" in why["a task in a callout"]
    assert "numbered" in why["a numbered task"]
    assert "block id" in why["linked from elsewhere"]
    assert "runs on" in why["runs on"] or "cannot tell" in why["runs on"]


def test_tasks_in_tiro_blocks_frontmatter_and_code_are_not_tasks() -> None:
    text = ("---\ntiro: triage\n---\n```\n- [ ] in code\n```\n"
            "<!-- tiro:begin job=tasks id=a -->\n- [ ] Move to [[X]]: y <!-- tiro:move task=1 to=X.md hash=2 -->\n"
            "<!-- tiro:end id=a -->\n- [ ] the real one\n")
    assert [t.text for t in tasks.tasks(text)] == ["the real one"]


# --- placing and checking ------------------------------------------------


def test_a_task_lands_after_the_last_task_of_the_first_task_list() -> None:
    text = "# Hub\n\n- [ ] a\n    - under a\n- plain item\n- [x] b\n\nProse.\n\n- [ ] later list\n"
    out = tasks.insert(text, ["- [ ] new", "    - its child"])
    assert out == ("# Hub\n\n- [ ] a\n    - under a\n- plain item\n- [x] b\n- [ ] new\n"
                   "    - its child\n\nProse.\n\n- [ ] later list\n")


def test_with_no_task_list_a_task_lands_at_the_end_before_tiros_blocks() -> None:
    text = ("Just prose.\n\n<!-- tiro:begin job=triage id=a -->\n> x\n"
            "<!-- tiro:end id=a -->\n")
    out = tasks.insert(text, ["- [ ] new"])
    assert out == ("Just prose.\n\n- [ ] new\n\n<!-- tiro:begin job=triage id=a -->\n> x\n"
                   "<!-- tiro:end id=a -->\n")
    assert tasks.insert("No newline at the end", ["- [ ] new"]) == (
        "No newline at the end\n\n- [ ] new")


def test_the_check_refuses_anything_but_the_moved_lines() -> None:
    task = tasks.tasks(DAILY)[1]
    source = tasks.remove(DAILY, [task])
    dest = tasks.insert(HUB_TEXT, list(task.lines))
    into = {HUB: [list(task.lines)]}
    assert tasks.check(DAILY, source, [list(task.lines)], {HUB: HUB_TEXT}, {HUB: dest}, into) == []

    reworded = dest.replace("Call the dentist", "Call the dentist today")
    edited = source.replace("Some prose", "Other prose")
    failures = tasks.check(DAILY, edited, [list(task.lines)], {HUB: HUB_TEXT},
                           {HUB: reworded}, into)
    assert len(failures) == 3


def test_tiros_own_landings_chain_and_the_users_edits_do_not() -> None:
    state: dict = {}
    tasks.remember_landing(state, HUB, "h0", "h1")
    tasks.remember_landing(state, HUB, "h1", "h2")
    assert tasks.only_landed(state, HUB, "h0", "h2")
    assert not tasks.only_landed(state, HUB, "h0", "h3")
    assert not tasks.only_landed(state, OTHER, "h0", "h1")


# --- proposing -----------------------------------------------------------


def test_triage_proposes_a_destination_or_a_reason_for_every_open_task(ready, vault) -> None:
    hub_before = (vault / HUB).read_bytes()
    _triage(ready)
    body = _block(vault)

    moves = [l for l in body.split("\n") if l.startswith("- [ ] Move to")]
    assert len(moves) == 2
    assert all("[[JupyterHub next]]" in l for l in moves)
    assert "Fix the kernel spawner #jupyterhub" in moves[0]
    assert "📅" not in body  # the plugin must not read Tiro's line as due
    assert "Call the dentist — a personal errand" in body
    assert "Merge the docs PR" not in body  # finished tasks are never proposed
    # Nothing moved: the destination is byte for byte as it was, and every
    # task is still where the user wrote it.
    assert (vault / HUB).read_bytes() == hub_before
    assert protocol.user_content((vault / DAY).read_text()) == protocol.user_content(DAILY)


def test_no_tick_means_no_move_ever(ready, vault) -> None:
    _triage(ready)
    day, hub = (vault / DAY).read_bytes(), (vault / HUB).read_bytes()
    _age(vault)
    record = _carry(ready)
    assert record.entries == []
    assert (vault / DAY).read_bytes() == day and (vault / HUB).read_bytes() == hub


@pytest.mark.parametrize("to, why", [
    ("Roam/Old tasks.md", "closed to moved tasks"),
    ("2026-10-16.md", "daily note"),
    ("Projects/Nowhere.md", "no such note"),
    ("Areas/orphan.md", "L1"),
    ("Tiro/Journal.md", "Tiro's own"),
    ("../outside.md", "outside the vault"),
])
def test_a_destination_that_fails_a_check_is_never_proposed(ready, vault, to, why) -> None:
    _write(vault, "2026-10-16.md", "- [ ] yesterday\n")
    _write(vault, "Tiro/Journal.md", "- [ ] x\n")
    _age(vault)
    _triage(ready, [{"n": 1, "to": to, "why": "x"}])
    body = _block(vault)
    assert "- [ ] Move to" not in body
    assert why in body


# --- moving --------------------------------------------------------------


def test_a_ticked_move_is_made_verbatim_and_only_that_one(ready, vault) -> None:
    _triage(ready)
    _tick(vault, "Fix the kernel spawner")
    record = _carry(ready)

    day = (vault / DAY).read_text()
    hub = (vault / HUB).read_text()
    first = tasks.tasks(DAILY)[0]
    assert "\n".join(first.lines) not in day.split("<!-- tiro:begin")[0]
    assert hub == HUB_TEXT.replace("- [x] Pin the image ✅ 2026-09-30\n",
                                   "- [x] Pin the image ✅ 2026-09-30\n"
                                   + "\n".join(first.lines) + "\n")
    # The other proposal is untouched and still waiting for its own tick.
    assert "- [ ] Write the upgrade note ⏫" in day
    assert "- [ ] Move to [[JupyterHub next]]: Write the upgrade note" in day
    # The source's block says where it went.
    assert re.search(r"\*\*Moved\*\*\n- 2026-\d\d-\d\d: Fix the kernel spawner #jupyterhub "
                     r"→ \[\[JupyterHub next\]\]", _block(vault))
    [entry] = record.entries
    assert entry.verb == "tasks" and entry.outcome == "done"
    assert entry.moves == [("Fix the kernel spawner #jupyterhub", DAY, HUB)]


def test_the_journal_lists_every_move_with_source_and_destination(ready, vault) -> None:
    _triage(ready)
    _tick(vault, "Fix the kernel spawner")
    _tick(vault, "Write the upgrade note")
    record = _carry(ready)
    journal = (vault / "Tiro" / "Journal" / f"{record.run_id[:10]}.md").read_text()
    assert ("- Fix the kernel spawner #jupyterhub: [[2026-10-17]] → "
            "[[Projects/JupyterHub next]]") in journal
    assert ("- Write the upgrade note: [[2026-10-17]] → "
            "[[Projects/JupyterHub next]]") in journal


def test_a_batch_is_one_commit_and_reverting_it_restores_both_notes(ready, vault) -> None:
    git = Git(vault)
    _triage(ready)
    _tick(vault, "Fix the kernel spawner")
    _tick(vault, "Write the upgrade note")
    day, hub = (vault / DAY).read_bytes(), (vault / HUB).read_bytes()
    record = _carry(ready)

    [sha] = [s for s in git.commits_for_run(record.run_id)
             if "Tiro-Job: tasks" in git("log", "-1", "--format=%B", s)]
    assert set(git("show", "--name-only", "--format=", sha).split("\n")) - {""} == {DAY, HUB}
    hub_after = (vault / HUB).read_text()
    assert hub_after.index("Fix the kernel") < hub_after.index("Write the upgrade")

    git.revert([sha])
    assert (vault / DAY).read_bytes() == day
    assert (vault / HUB).read_bytes() == hub


def test_a_destination_changed_since_the_proposal_is_proposed_again(ready, vault) -> None:
    _triage(ready)
    _tick(vault, "Fix the kernel spawner")
    _write(vault, HUB, HUB_TEXT + "\nThe user added this.\n")
    _age(vault)
    hub = (vault / HUB).read_bytes()

    record = _carry(ready)

    assert (vault / HUB).read_bytes() == hub
    body = _block(vault)
    line = next(l for l in body.split("\n") if "Fix the kernel spawner" in l)
    assert line.startswith("- [ ] Move to")  # unticked, with a fresh hash
    assert "changed since this was proposed" in body
    assert record.entries[0].outcome == "needs-input"

    _tick(vault, "Fix the kernel spawner")
    _carry(ready)
    assert "Fix the kernel spawner" in (vault / HUB).read_text()


def test_two_notes_can_each_move_a_task_into_the_same_note(ready, vault) -> None:
    second = "2026-10-16.md"
    _write(vault, second, DAILY.replace("Friday", "Thursday").replace("kernel", "proxy"))
    _age(vault)
    _triage(ready)
    _triage(ready, note=second)
    _tick(vault, "Fix the kernel spawner")
    _tick(vault, "Fix the proxy spawner", second)

    _carry(ready)
    _carry(ready, second)  # proposed against the hub before the first landing

    hub = (vault / HUB).read_text()
    assert "Fix the kernel spawner" in hub and "Fix the proxy spawner" in hub


def test_a_task_that_changed_since_the_proposal_is_not_moved(ready, vault) -> None:
    _triage(ready)
    _tick(vault, "Fix the kernel spawner")
    path = vault / DAY
    path.write_text(path.read_text().replace("📅 2026-10-20", "📅 2026-10-27"), encoding="utf-8")
    _age(vault)
    hub = (vault / HUB).read_bytes()

    _carry(ready)

    assert (vault / HUB).read_bytes() == hub
    assert "no longer in this note as it was proposed" in _block(vault)


def test_a_destination_closed_after_the_proposal_is_refused_and_said(ready, vault) -> None:
    _triage(ready)
    _tick(vault, "Fix the kernel spawner")
    closed = replace(ready, trust=TrustMap(default="L1", folders={"/": "L2", "Projects/": "L2"}))
    hub = (vault / HUB).read_bytes()

    _carry(closed)

    assert (vault / HUB).read_bytes() == hub
    body = _block(vault)
    assert "Not moved" in body and "L2" in body
    assert "- [x]" not in body  # unticked, so it does not fire every run


def test_naming_another_note_on_the_line_is_a_new_proposal(ready, vault) -> None:
    _triage(ready)
    path = vault / DAY
    path.write_text(path.read_text().replace(
        "- [ ] Move to [[JupyterHub next]]: Write", "- [x] Move to [[Teaching]]: Write"),
        encoding="utf-8")
    _age(vault)

    _carry(ready)

    assert "Write the upgrade note" not in (vault / OTHER).read_text()
    line = next(l for l in _block(vault).split("\n") if "Write the upgrade note" in l)
    assert line.startswith("- [ ] Move to [[Teaching]]")
    _tick(vault, "Write the upgrade note")
    _carry(ready)
    assert "Write the upgrade note" in (vault / OTHER).read_text()


def test_a_task_landing_does_not_make_its_destination_look_edited(ready, vault) -> None:
    # The hub asks for triage itself and has been seen: Tiro adding a task to
    # it must not queue that triage again, or Tiro triggers Tiro.
    path = vault / HUB
    text = protocol.set_key(HUB_TEXT, "tiro", "triage")
    text = protocol.mark_done(text, "triage")
    path.write_text(protocol.set_key(text, "tiro/hash", protocol.user_hash(text)), encoding="utf-8")
    _triage(ready)
    _tick(vault, "Fix the kernel spawner")
    record = runner.once(ready, agent=ScriptedAgent({}), ops=FilesystemOps(vault),
                         only={DAY, HUB})
    assert [e.verb for e in record.entries] == ["tasks"]
    state = runner._load_state(ready)
    jobs, _ = runner.scan(ready, now=10**10, state=state)
    assert HUB not in {j.rel for j in jobs}


def test_auto_mode_does_not_look_again_at_a_note_a_task_landed_in(ready, vault) -> None:
    path = vault / HUB
    path.write_text(protocol.set_key(HUB_TEXT, "tiro/hash", protocol.user_hash(HUB_TEXT)),
                    encoding="utf-8")
    _triage(ready)
    _tick(vault, "Fix the kernel spawner")
    _carry(ready)
    looking = replace(ready, auto=AutoConfig(folders=("Projects/",), settle_minutes=0))
    state = runner._load_state(ready)
    auto.switch_on(state, now=1.0)

    jobs, _ = auto.scan(looking, state, now=10**10)
    assert HUB not in {j.rel for j in jobs}

    path.write_text(path.read_text() + "\nThe user writes here.\n", encoding="utf-8")
    jobs, _ = auto.scan(looking, state, now=10**10)
    assert HUB in {j.rel for j in jobs}


def test_undo_takes_the_move_back_and_unticks_it(ready, vault) -> None:
    _triage(ready)
    _tick(vault, "Fix the kernel spawner")
    hub = (vault / HUB).read_bytes()
    record = _carry(ready)
    assert (vault / HUB).read_bytes() != hub

    rc = cli.main(["--root", str(ready.root), "--vault", str(vault), "undo", record.run_id])
    assert rc == 0
    assert (vault / HUB).read_bytes() == hub
    body = _block(vault)
    assert "- [x]" not in body and "Undone with `tiro undo" in body
    _age(vault)
    assert _carry(ready).entries == []  # the undone move is not made again
