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
