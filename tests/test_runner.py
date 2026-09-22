"""The loop, end to end, with a scripted agent instead of a model.

Everything here is real except the thinking: real notes, real git, real gate,
real journal. That is the point of making the agent read-only and structured —
the parts that can damage a vault are testable without spending a token.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tiro import protocol, runner
from tiro.agent import AgentError, JobOutput, ScriptedAgent
from tiro.ops_fs import FilesystemOps
from tiro.vcs import Git

NOTE = "00 Inbox/bitter-lesson.md"
OLD = 0.0  # old enough that the "user may be typing" guard does not fire


def _agent(**results: JobOutput | Exception) -> ScriptedAgent:
    return ScriptedAgent(dict(results))


def _run(config, agent, **kw):
    return runner.once(config, agent=agent, ops=FilesystemOps(config.vault), **kw)


def _age(vault: Path) -> None:
    """Backdate every note so the skip-if-recent guard does not swallow the run."""
    import os

    for path in vault.rglob("*.md"):
        os.utime(path, (OLD, OLD))


@pytest.fixture
def ready(config, vault: Path):
    _age(vault)
    return config


# --- the shape of a run ---------------------------------------------------


def test_a_job_writes_a_block_and_makes_one_commit(ready, vault: Path) -> None:
    git = Git(vault)
    before = len(git("log", "--format=%H").split())

    record = _run(ready, _agent(
        research=JobOutput(block="> [!abstract] Tiro\n> The answer.", detail="6 sources"),
        triage=JobOutput(block="> [!abstract] Tiro\n> A note.", detail="proposed a home"),
    ))

    text = (vault / NOTE).read_text()
    assert "The answer." in text
    assert protocol.read_keys(text)["tiro/status"] == "done"
    # one commit per job, plus one for the journal
    after = len(git("log", "--format=%H").split())
    assert after == before + len(record.entries) + 1
    assert all(e.outcome == "done" for e in record.entries)


def test_running_twice_over_an_unchanged_vault_does_nothing_the_second_time(ready, vault) -> None:
    out = {"research": JobOutput(block="x", detail="d"), "triage": JobOutput(block="y", detail="d")}
    _run(ready, ScriptedAgent(dict(out)))
    _age(vault)
    git = Git(vault)
    before = git.head()

    second = _run(ready, ScriptedAgent(dict(out)))

    assert second.entries == []
    # only the journal commit, and nothing touched a note
    assert "Tiro-Job: journal" in git("log", "-1", "--format=%B")
    assert before != git.head()  # the journal still recorded the quiet run


def test_a_user_edit_after_a_run_brings_the_note_back(ready, vault: Path) -> None:
    out = {"research": JobOutput(block="x", detail="d"), "triage": JobOutput(block="y", detail="d")}
    _run(ready, ScriptedAgent(dict(out)))

    path = vault / NOTE
    path.write_text(path.read_text() + "\nAnd a follow-up question?\n", encoding="utf-8")
    _age(vault)

    second = _run(ready, ScriptedAgent(dict(out)))
    assert [e.note for e in second.entries] == [NOTE]
    assert second.entries[0].detail == "d"


def test_the_commit_carries_trailers_that_make_undo_possible(ready, vault: Path) -> None:
    record = _run(ready, _agent(research=JobOutput(block="x", detail="d"),
                                triage=JobOutput(block="y", detail="d")))
    git = Git(vault)
    shas = git.commits_for_run(record.run_id)
    assert len(shas) == len(record.entries) + 1


def test_undo_restores_the_vault(ready, vault: Path) -> None:
    git = Git(vault)
    before = (vault / NOTE).read_text()
    record = _run(ready, _agent(research=JobOutput(block="x", detail="d"),
                                triage=JobOutput(block="y", detail="d")))
    assert (vault / NOTE).read_text() != before

    git.revert(git.commits_for_run(record.run_id))
    assert (vault / NOTE).read_text() == before


# --- failure is visible, never silent ------------------------------------


def test_an_agent_failure_blocks_the_note_and_says_why(ready, vault: Path) -> None:
    record = _run(ready, _agent(
        research=AgentError("the model produced no result block"),
        triage=JobOutput(block="y", detail="d"),
    ))
    text = (vault / NOTE).read_text()
    assert protocol.read_keys(text)["tiro/status"] == "blocked"
    assert "no result block" in text
    assert any(e.outcome == "blocked" for e in record.entries)


def test_a_blocked_note_is_not_retried_forever(ready, vault: Path) -> None:
    for _ in range(5):
        _age(vault)
        _run(ready, _agent(research=AgentError("nope"), triage=JobOutput(block="y")))
    attempts = runner._load_state(ready)["attempts"]
    assert max(attempts.values()) <= ready.run.max_attempts_per_note_per_day


def test_an_unknown_verb_is_blocked_with_a_useful_message(ready, vault: Path) -> None:
    path = vault / "00 Inbox/typo.md"
    path.write_text("---\ntiro: reserch\n---\n\nbody\n", encoding="utf-8")
    _age(vault)

    record = _run(ready, _agent(research=JobOutput(block="x"), triage=JobOutput(block="y")))

    entry = next(e for e in record.entries if e.note.endswith("typo.md"))
    assert entry.outcome == "blocked"
    assert "unknown verb" in entry.detail


def test_a_note_edited_mid_job_is_left_alone_and_retried(ready, vault: Path) -> None:
    path = vault / NOTE

    class Meddler(ScriptedAgent):
        def run(self, request):
            path.write_text(path.read_text() + "\nthe user types\n", encoding="utf-8")
            return super().run(request)

    record = _run(ready, Meddler({"research": JobOutput(block="x"), "triage": JobOutput(block="y")}))

    entry = next(e for e in record.entries if e.note == NOTE)
    assert entry.outcome == "skipped"
    assert "the user edited" in entry.detail
    assert "tiro/status" not in protocol.read_keys(path.read_text())


# --- note content is data ------------------------------------------------


def test_a_note_telling_tiro_to_delete_things_is_just_a_note(ready, vault: Path) -> None:
    record = _run(ready, _agent(
        triage=JobOutput(block="> [!abstract] Tiro\n> A note containing an instruction.",
                         detail="classified"),
        research=JobOutput(block="x"),
    ))
    hostile = next(e for e in record.entries if e.note.endswith("hostile.md"))
    assert hostile.outcome == "done"
    assert (vault / "Areas/Compute trends.md").exists()
    assert (vault / "Areas/orphan.md").exists()


# --- the user-facing surface ---------------------------------------------


def test_the_journal_names_every_job_and_links_the_note(ready, vault: Path) -> None:
    record = _run(ready, _agent(research=JobOutput(block="x", detail="six sources"),
                                triage=JobOutput(block="y", detail="proposed a home")))
    journal = (vault / "Tiro/Journal" / f"{record.run_id[:10]}.md").read_text()
    assert "six sources" in journal
    assert "[[00 Inbox/bitter-lesson]]" in journal


def test_questions_indexes_notes_waiting_on_the_user(ready, vault: Path) -> None:
    _run(ready, _agent(
        research=JobOutput(status="needs-input",
                           block="> [!question] Tiro asks\n> Which sense of scaling?",
                           detail="asked"),
        triage=JobOutput(block="y"),
    ))
    questions = (vault / "Tiro/Questions.md").read_text()
    assert "[[00 Inbox/bitter-lesson]]" in questions
    assert "Which sense of scaling?" in questions


def test_a_note_waiting_on_the_user_is_not_picked_up_again(ready, vault: Path) -> None:
    out = {"research": JobOutput(status="needs-input", block="> [!question] Tiro asks\n> Which?"),
           "triage": JobOutput(block="y")}
    _run(ready, ScriptedAgent(dict(out)))
    _age(vault)
    second = _run(ready, ScriptedAgent(dict(out)))
    assert not any(e.note == NOTE for e in second.entries)
    assert any(s["rel"] == NOTE for s in second.skipped)


def test_a_crashed_run_is_reset_rather_than_stuck(ready, vault: Path) -> None:
    path = vault / NOTE
    path.write_text(protocol.set_key(path.read_text(), "tiro/status", "working"), encoding="utf-8")
    _age(vault)

    record = _run(ready, _agent(research=JobOutput(block="x", detail="d"),
                                triage=JobOutput(block="y")))
    assert any("crashed run" in n for n in record.notes)
    assert protocol.read_keys(path.read_text())["tiro/status"] == "done"


def test_the_run_record_says_which_backend_was_used(ready, vault: Path) -> None:
    record = _run(ready, _agent(research=JobOutput(block="x"), triage=JobOutput(block="y")))
    assert record.ops_backend == "filesystem"
    assert (vault / ".tiro/runs" / record.run_id / "run.json").exists()


# --- filing ---------------------------------------------------------------


def test_file_refuses_without_the_obsidian_cli(ready, vault: Path) -> None:
    path = vault / "00 Inbox/to-file.md"
    path.write_text("---\ntiro: file\ntiro/filed-to: Tiro/to-file.md\n---\n\nbody\n", encoding="utf-8")
    _age(vault)

    record = _run(ready, _agent(
        file=JobOutput(keys={"tiro/filed-to": "Tiro/to-file.md"}, block="x"),
        research=JobOutput(block="x"), triage=JobOutput(block="y"),
    ))

    entry = next(e for e in record.entries if e.note.endswith("to-file.md"))
    assert entry.outcome == "blocked"
    assert "Obsidian CLI" in entry.detail
    assert path.exists()  # nothing was moved


# --- the tag is the consent ----------------------------------------------


def test_a_tagged_note_runs_wherever_it_is_unless_the_folder_is_closed(ready, vault: Path) -> None:
    """Areas/ is L1 in the fixture, Private/ is L0. The first runs, the second
    is blocked and says why."""
    (vault / "Areas/loose.md").write_text("---\ntiro: research\n---\n\nwhat is this?\n", encoding="utf-8")
    (vault / "Private").mkdir()
    diary = vault / "Private/diary.md"
    diary.write_text("---\ntiro: research\n---\n\nmine\n", encoding="utf-8")
    _age(vault)

    record = _run(ready, _agent(research=JobOutput(block="answer", detail="d"),
                                triage=JobOutput(block="y")))

    by_note = {e.note: e for e in record.entries}
    assert by_note["Areas/loose.md"].outcome == "done"
    # The L0 note is refused in the journal and nowhere else: not even a
    # "blocked" block, because that would be the write L0 forbids.
    assert "Private/diary.md" not in by_note
    skip = next(s for s in record.skipped if s["rel"] == "Private/diary.md")
    assert "L0" in skip["why"]
    assert diary.read_text() == "---\ntiro: research\n---\n\nmine\n"
    journal = (vault / "Tiro/Journal" / f"{record.run_id[:10]}.md").read_text()
    assert "[[Private/diary]]" in journal


def test_file_moves_where_the_user_accepted_not_where_the_skill_says(ready, vault: Path) -> None:
    path = vault / "00 Inbox/to-file.md"
    path.write_text("---\ntiro: file\ntiro/filed-to: Tiro/to-file.md\n---\n\nbody\n", encoding="utf-8")
    _age(vault)

    record = _run(ready, _agent(
        file=JobOutput(keys={"tiro/filed-to": "Tiro/elsewhere.md"}, block="x"),
        research=JobOutput(block="x"), triage=JobOutput(block="y"),
    ))

    entry = next(e for e in record.entries if e.note.endswith("to-file.md"))
    assert entry.outcome == "blocked"
    assert "the note wins" in entry.detail
    assert path.exists()


def test_a_refused_move_leaves_no_empty_folder_behind(ready, vault: Path) -> None:
    path = vault / "00 Inbox/to-file.md"
    path.write_text("---\ntiro: file\ntiro/filed-to: Tiro/New Area/to-file.md\n---\n\nbody\n",
                    encoding="utf-8")
    _age(vault)

    _run(ready, _agent(
        file=JobOutput(keys={"tiro/filed-to": "Tiro/New Area/to-file.md"}, block="x"),
        research=JobOutput(block="x"), triage=JobOutput(block="y"),
    ))

    assert path.exists()
    assert not (vault / "Tiro/New Area").exists()


@pytest.mark.parametrize("destination, why", [
    ("../escaped.md", "outside the vault"),
    ("/tmp/escaped.md", "outside the vault"),
    ("Tiro/no-suffix", "not a markdown note"),
    (".obsidian/plugins/x.md", "hidden or reserved"),
])
def test_a_destination_that_leaves_the_vault_is_refused(ready, vault: Path, destination, why) -> None:
    """The destination is note content, and note content is the injection
    channel. Never #2 has to hold against `../`."""
    path = vault / "00 Inbox/to-file.md"
    path.write_text(f"---\ntiro: file\ntiro/filed-to: {destination}\n---\n\nbody\n", encoding="utf-8")
    _age(vault)

    record = _run(ready, _agent(
        file=JobOutput(keys={"tiro/filed-to": destination}, block="x"),
        research=JobOutput(block="x"), triage=JobOutput(block="y"),
    ))

    entry = next(e for e in record.entries if e.note.endswith("to-file.md"))
    assert entry.outcome == "blocked"
    assert why in entry.detail
    assert path.exists()
    assert not (vault.parent / "escaped.md").exists()
    assert not Path("/tmp/escaped.md").exists()


def test_tiros_own_report_cannot_queue_a_job(ready, vault: Path) -> None:
    """Health.md says "try `#tiro research`". Read literally, lint would file
    a research request against its own report every run."""
    from tiro import scan as scan_mod

    (vault / "Tiro").mkdir(exist_ok=True)
    (vault / "Tiro/Health.md").write_text("- almost: try `#tiro research`\n#tiro/research\n",
                                          encoding="utf-8")
    jobs, _ = scan_mod.scan(ready, now=1e12)
    assert not any(j.rel.startswith("Tiro/") for j in jobs)


def test_an_unknown_verb_in_a_closed_folder_is_not_written_either(ready, vault: Path) -> None:
    (vault / "Private").mkdir()
    diary = vault / "Private/diary.md"
    diary.write_text("---\ntiro: frobnicate\n---\n\nmine\n", encoding="utf-8")
    _age(vault)

    _run(ready, _agent(research=JobOutput(block="x"), triage=JobOutput(block="y")))

    assert diary.read_text() == "---\ntiro: frobnicate\n---\n\nmine\n"


def test_an_unexpected_failure_blocks_the_note_and_the_run_goes_on(ready, vault: Path) -> None:
    """Never #4: a crash in one job is a blocked note with a reason, not a run
    that ends before the journal is written."""
    class Faulty(ScriptedAgent):
        def run(self, request):
            if request.verb == "research":
                raise RuntimeError("the network fell over")
            return super().run(request)

    record = _run(ready, Faulty({"research": JobOutput(block="x"), "triage": JobOutput(block="y")}))

    by_note = {e.note: e for e in record.entries}
    assert by_note[NOTE].outcome == "blocked"
    assert "RuntimeError" in by_note[NOTE].detail
    assert any(e.outcome == "done" for e in record.entries)  # the triage jobs still ran
    text = (vault / NOTE).read_text()
    assert protocol.read_keys(text)["tiro/status"] == "blocked"
    assert "the network fell over" in text
    journal = (vault / "Tiro/Journal" / f"{record.run_id[:10]}.md").read_text()
    assert "RuntimeError" in journal


def test_changing_the_verb_is_an_edit(ready, vault: Path) -> None:
    """triage → file is the accept the filing flow waits for. If the verb sat
    outside the hash, the accept would never be noticed."""
    out = {"research": JobOutput(block="x"), "triage": JobOutput(block="y", detail="proposed"),
           "file": JobOutput(block="z")}
    _run(ready, ScriptedAgent(dict(out)))
    hostile = vault / "00 Inbox/hostile.md"
    hostile.write_text(hostile.read_text().replace("tiro: triage", "tiro: file"), encoding="utf-8")
    _age(vault)

    second = _run(ready, ScriptedAgent(dict(out)))
    assert [e.verb for e in second.entries if e.note.endswith("hostile.md")] == ["file"]


def test_the_agent_may_not_write_the_users_verb() -> None:
    """Otherwise spec could queue its own dispatch, and "only the user releases
    it" would be a sentence in a prompt rather than a property."""
    from tiro.agent import JobOutput as Out

    with pytest.raises(AgentError, match="only the user"):
        Out.from_json({"keys": {"tiro": "dispatch"}})
    assert Out.from_json({"keys": {"tiro/filed-to": "x.md"}}).keys == {"tiro/filed-to": "x.md"}


def test_without_rules_the_skill_is_told_to_infer_rather_than_ask(ready, vault: Path) -> None:
    agent = _agent(research=JobOutput(block="x"), triage=JobOutput(block="y"))
    _run(ready, agent)
    assert any("no `.tiro/rules.md` yet" in call.skill for call in agent.calls)

    (vault / ".tiro/rules.md").write_text("### R-001 — a rule\n", encoding="utf-8")
    (vault / NOTE).write_text((vault / NOTE).read_text() + "\nmore\n", encoding="utf-8")
    _age(vault)
    agent = _agent(research=JobOutput(block="x"), triage=JobOutput(block="y"))
    _run(ready, agent)
    assert all("read it first" in call.skill for call in agent.calls)


# --- what a run cost ------------------------------------------------------


def test_usage_is_read_from_the_sdks_dict_not_with_getattr() -> None:
    """The SDK's `usage` is a dict. It was read with getattr, which returns the
    default for every key, so every run reported zero tokens."""
    from tiro.agent import Usage

    raw = {"input_tokens": 1200, "output_tokens": 340,
           "cache_read_input_tokens": 8000, "cache_creation_input_tokens": 50}
    u = Usage.from_sdk(raw)
    assert (u.input_tokens, u.output_tokens) == (1200, 340)
    assert (u.cache_read_tokens, u.cache_write_tokens) == (8000, 50)
    assert u.total_tokens == 9590
    assert Usage.from_sdk(None).total_tokens == 0
    assert Usage.from_sdk({}).cost_usd is None


def test_an_unreported_cost_is_not_reported_as_free() -> None:
    """On a subscription the SDK often gives no dollar figure. Calling that
    $0.00 is how a budget goes unnoticed."""
    from tiro.agent import Usage
    from tiro.journal import spend

    assert "cost not reported" in spend(Usage(output_tokens=10))
    assert "$0.40" in spend(Usage(output_tokens=10, cost_usd=0.4))
    assert spend(Usage()) == "No model time."


def test_a_runs_usage_is_the_sum_of_its_jobs(ready, vault: Path) -> None:
    """The run record's totals were fields nobody ever assigned."""
    from tiro.agent import Usage

    record = _run(ready, _agent(
        research=JobOutput(block="x", detail="d",
                           usage=Usage(input_tokens=100, output_tokens=20, cost_usd=0.05)),
        triage=JobOutput(block="y", detail="d",
                         usage=Usage(input_tokens=10, output_tokens=5, cost_usd=0.01)),
    ))

    jobs = len(record.entries)
    assert jobs >= 2
    assert record.usage.total_tokens == sum(e.usage.total_tokens for e in record.entries)
    assert record.usage.total_tokens > 0
    assert record.usage.cost_usd == pytest.approx(
        sum(e.usage.cost_usd or 0 for e in record.entries))

    import json
    saved = json.loads((vault / ".tiro/runs" / record.run_id / "run.json").read_text())
    # The numbers the user complained about, now written per job and per run.
    assert saved["usage"]["output_tokens"] == record.usage.output_tokens > 0
    assert saved["usage"]["cost_usd"] == pytest.approx(record.usage.cost_usd)
    assert saved["entries"][0]["usage"]["input_tokens"] > 0
    assert "tokens" not in saved  # the old always-zero fields are gone
    assert "cost_usd" not in saved

    journal = (vault / "Tiro/Journal" / f"{record.run_id[:10]}.md").read_text()
    assert "tokens" in journal and "$" in journal


# --- the daily ceiling, which is what makes a timer safe to leave on ------


def test_a_run_stops_once_the_days_budget_is_spent(ready, vault: Path) -> None:
    from dataclasses import replace

    from tiro.agent import Usage
    from tiro.config import RunConfig

    config = replace(ready, run=replace(ready.run, max_tokens_per_day=100))
    pricey = JobOutput(block="x", detail="d", usage=Usage(output_tokens=90))

    first = _run(config, _agent(research=pricey, triage=pricey, file=pricey))
    # A job's cost is not knowable before it runs, so the ceiling is "stop once
    # past it" and may overshoot by exactly one job: 90 is under 100 so the
    # second starts, 180 is over so the third never does. The fixture has three
    # taggable notes, so two running is the documented behaviour, not a leak.
    assert len(first.entries) == 2
    assert any("token ceiling is spent" in n for n in first.notes)

    _age(vault)
    second = _run(config, _agent(research=pricey, triage=pricey, file=pricey))
    assert second.entries == []
    assert any("token ceiling is spent" in n for n in second.notes)

    journal = (vault / "Tiro/Journal" / f"{second.run_id[:10]}.md").read_text()
    assert "stopped early" in journal


def test_the_cost_ceiling_works_the_same_way(ready) -> None:
    from dataclasses import replace

    from tiro.agent import Usage
    from tiro import runner as r

    config = replace(ready, run=replace(ready.run, max_cost_usd_per_day=0.10))
    state = {"attempts": {}, "spend": {}}
    assert r.over_budget(config, state, "2026-09-22") is None
    r.record_spend(state, "2026-09-22", Usage(output_tokens=5, cost_usd=0.04))
    assert r.over_budget(config, state, "2026-09-22") is None
    r.record_spend(state, "2026-09-22", Usage(output_tokens=5, cost_usd=0.07))
    assert "cost ceiling" in r.over_budget(config, state, "2026-09-22")
    # Yesterday's spend does not count against today.
    assert r.over_budget(config, state, "2026-09-23") is None


def test_no_ceiling_configured_means_no_ceiling(ready) -> None:
    from tiro.agent import Usage
    from tiro import runner as r

    state = {"attempts": {}, "spend": {}}
    for _ in range(50):
        r.record_spend(state, "2026-09-22", Usage(output_tokens=100000, cost_usd=99.0))
    assert ready.run.max_tokens_per_day is None
    assert r.over_budget(ready, state, "2026-09-22") is None


def test_spend_is_written_as_each_job_finishes(ready, vault: Path) -> None:
    """A run killed mid-pass must still have spent what it spent, as far as
    tomorrow's ceiling is concerned."""
    from tiro.agent import Usage
    from tiro import runner as r

    record = _run(ready, _agent(
        research=JobOutput(block="x", usage=Usage(output_tokens=7, cost_usd=0.02)),
        triage=JobOutput(block="y", usage=Usage(output_tokens=3, cost_usd=0.01)),
    ))
    day = record.run_id[:10]
    today = r._load_state(ready)["spend"][day]
    assert today["tokens"] == record.usage.total_tokens > 0
    assert today["jobs"] == len(record.entries)


def test_a_failed_push_is_reported_in_the_journal(ready, vault: Path) -> None:
    """A vault that has quietly stopped syncing must say so where the user
    looks, not only on a terminal a timer does not have."""
    from tiro.vcs import Git

    class Offline(Git):
        """A remote that accepts a rebase (there is nothing to fetch) and
        refuses a push, which is what a dead network or a bad key looks like."""

        def has_remote(self) -> bool:
            return True

        def pull_rebase(self):
            return True, "up to date"

        def push(self):
            return False, "Could not read from remote repository"

    record = runner.once(ready, agent=_agent(research=JobOutput(block="x"),
                                             triage=JobOutput(block="y")),
                         ops=FilesystemOps(vault), git=Offline(vault))

    assert any("could not push" in n for n in record.notes)
    journal = (vault / "Tiro/Journal" / f"{record.run_id[:10]}.md").read_text()
    assert "could not push" in journal
    assert "Could not read from remote repository" in journal


# --- what Obsidian does to *other* notes when it moves one ---------------


class _RewritingOps(FilesystemOps):
    """Obsidian's move, as far as the rest of the vault can tell: the note
    moves, and every note that linked to it has its link rewritten."""

    def move(self, src_rel: str, dst_rel: str) -> None:
        old, new = Path(src_rel).stem, dst_rel[:-3]
        (self.vault / dst_rel).parent.mkdir(parents=True, exist_ok=True)
        (self.vault / src_rel).rename(self.vault / dst_rel)
        for path in self.vault.rglob("*.md"):
            if path == self.vault / dst_rel:
                continue
            text = path.read_text(encoding="utf-8")
            if f"[[{old}]]" in text:
                path.write_text(text.replace(f"[[{old}]]", f"[[{new}]]"), encoding="utf-8")


def _linked_pair(vault: Path, destination: str) -> str:
    """A note with a single inbound link and no outgoing ones, so the only
    thing in play is Obsidian rewriting somebody else's file."""
    src = "Areas/target.md"
    (vault / src).write_text(
        f"---\ntiro: file\ntiro/filed-to: {destination}\n---\n\nthe note being filed\n",
        encoding="utf-8")
    (vault / "Areas/pointer.md").write_text("points at [[target]]\n", encoding="utf-8")
    Git(vault)("add", "-A")
    Git(vault)("commit", "-qm", "a linked note")
    return src


def test_filing_a_linked_note_survives_obsidians_link_rewrites(ready, vault: Path) -> None:
    """Obsidian rewrites the link in Areas/pointer.md, a file the job never
    declared. That is part of the move, not a stray edit."""
    dst = "Tiro/target.md"
    src = _linked_pair(vault, dst)
    _age(vault)

    record = runner.once(ready, agent=_agent(
        file=JobOutput(keys={"tiro/filed-to": dst}, block="x", detail="filed"),
        research=JobOutput(block="x"), triage=JobOutput(block="y")),
        ops=_RewritingOps(vault))

    entry = next(e for e in record.entries if e.note == src)
    assert entry.outcome == "done", entry.detail
    assert (vault / dst).exists() and not (vault / src).exists()
    # The rewrite went in with the move, in the same commit, and Areas/ is L1 —
    # a link rewrite is not a write the write-trust rule should judge.
    assert "[[Tiro/target]]" in (vault / "Areas/pointer.md").read_text()
    # Nothing left uncommitted but Tiro's own run state, which the vault's
    # .gitignore covers in a real vault and the fixture has none of.
    assert [p for p in Git(vault).dirty_paths() if not p.startswith(".tiro/")] == []


def test_a_failed_move_puts_the_rewritten_links_back_too(ready, vault: Path) -> None:
    """If the gate refuses after the move, every file the job touched goes
    back — not only the ones it declared. Otherwise the note returns and the
    rewritten links point at where it is not."""
    dst = "Tiro/target.md"
    src = _linked_pair(vault, dst)
    pointer = vault / "Areas/pointer.md"
    before = pointer.read_text()
    _age(vault)

    class Saboteur(_RewritingOps):
        """Moves, rewrites, and also scribbles on a note nobody declared."""

        def move(self, src_rel: str, dst_rel: str) -> None:
            super().move(src_rel, dst_rel)
            (self.vault / "Areas/orphan.md").write_text("clobbered\n", encoding="utf-8")

    record = runner.once(ready, agent=_agent(
        file=JobOutput(keys={"tiro/filed-to": dst}, block="x"),
        research=JobOutput(block="x"), triage=JobOutput(block="y")),
        ops=Saboteur(vault))

    entry = next(e for e in record.entries if e.note == src)
    assert entry.outcome == "blocked"
    assert "orphan" in entry.detail
    assert pointer.read_text() == before          # the rewrite is undone
    assert "clobbered" not in (vault / "Areas/orphan.md").read_text()
    assert (vault / src).exists() and not (vault / dst).exists()
