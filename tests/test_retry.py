"""Retry after a machine failure (ITERATION-2 M4).

A note blocked because the machine failed — no SDK, no network, Obsidian
closed, acli unreachable — retries by itself once the machine is back. A note
blocked because the agent could not do the job waits for the user. The two
used to look identical, and both needed a hand edit before anything retried.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

from tiro import protocol, runner
from tiro.agent import AgentError, AgentUnavailable, ClaudeAgentRunner, JobOutput, JobRequest, ScriptedAgent
from tiro.failure import is_machine
from tiro.jira import JiraDown, JiraError
from tiro.ops import OpsDown
from tiro.ops_fs import FilesystemOps

NOTE = "00 Inbox/bitter-lesson.md"


def _age(vault: Path) -> None:
    for path in vault.rglob("*.md"):
        os.utime(path, (0, 0))


def _run(config, **results):
    _age(config.vault)
    results.setdefault("triage", JobOutput(block="y"))
    return runner.once(config, agent=ScriptedAgent(results), ops=FilesystemOps(config.vault))


def _entry(record, rel=NOTE):
    return next(e for e in record.entries if e.note == rel)


def test_a_missing_sdk_blocks_without_a_hash_and_retries_once_installed(config, vault: Path) -> None:
    first = _run(config, research=AgentUnavailable("claude-agent-sdk is not installed"))

    assert _entry(first).outcome == "blocked"
    assert "retries by itself" in _entry(first).detail
    text = (vault / NOTE).read_text()
    assert "tiro/hash" not in protocol.read_keys(text)
    assert "will retry" in text

    # The dependency is installed. Nobody touches the note.
    second = _run(config, research=JobOutput(block="> [!abstract] the answer", detail="d"))
    assert _entry(second).outcome == "done"
    assert "the answer" in (vault / NOTE).read_text()


def test_a_job_the_agent_could_not_do_waits_for_the_user(config, vault: Path) -> None:
    first = _run(config, research=AgentError("agent produced no ```json result block"))
    assert _entry(first).outcome == "blocked"
    assert "retries by itself" not in _entry(first).detail
    assert protocol.read_keys((vault / NOTE).read_text()).get("tiro/hash")

    second = _run(config, research=JobOutput(block="x"))
    assert not any(e.note == NOTE for e in second.entries)


def test_a_dropped_connection_is_the_machine(config, vault: Path) -> None:
    """Raised straight out of the agent, past the handled exceptions, into the
    catch-all — which must still tell the machine from a bug."""
    first = _run(config, research=ConnectionError("network is unreachable"))
    assert "retries by itself" in _entry(first).detail
    assert "tiro/hash" not in protocol.read_keys((vault / NOTE).read_text())


def test_a_bug_is_not_the_machine(config, vault: Path) -> None:
    first = _run(config, research=KeyError("a bug in Tiro"))
    assert "retries by itself" not in _entry(first).detail
    assert protocol.read_keys((vault / NOTE).read_text()).get("tiro/hash")


def test_retrying_is_still_bounded_by_the_attempts_cap(config, vault: Path) -> None:
    for _ in range(6):
        _run(config, research=AgentUnavailable("still no network"))
    attempts = runner._load_state(config)["attempts"]
    assert max(attempts.values()) == config.run.max_attempts_per_note_per_day


def test_obsidian_being_closed_is_the_machine(config, vault: Path) -> None:
    """The commonest one on a timer: filing at night, with the app shut."""
    rel = "00 Inbox/to-file.md"
    (vault / rel).write_text("---\ntiro: file\ntiro/filed-to: Tiro/to-file.md\n---\n\nbody\n",
                             encoding="utf-8")
    record = _run(config, research=JobOutput(block="x"),
                  file=JobOutput(keys={"tiro/filed-to": "Tiro/to-file.md"}, block="x"))

    assert "retries by itself" in _entry(record, rel).detail
    assert "tiro/hash" not in protocol.read_keys((vault / rel).read_text())


@pytest.mark.parametrize("exc, machine", [
    (AgentUnavailable("x"), True),
    (AgentError("x"), False),
    (OpsDown("x"), True),
    (JiraDown("x"), True),
    (JiraError("payload has no summary"), False),
    (TimeoutError("x"), True),
    (ValueError("x"), False),
])
def test_what_counts_as_the_machine(exc, machine) -> None:
    assert is_machine(exc) is machine


def test_the_real_runner_reports_an_sdk_failure_as_the_machine(config, monkeypatch) -> None:
    """ClaudeAgentRunner itself, with a stand-in SDK whose query falls over the
    way a dropped connection does."""
    fake = types.ModuleType("claude_agent_sdk")

    class ClaudeAgentOptions:
        def __init__(self, **kw):
            self.kw = kw

    async def query(prompt, options):
        raise RuntimeError("Connection reset by peer")
        yield  # pragma: no cover - makes this an async generator

    fake.ClaudeAgentOptions = ClaudeAgentOptions
    fake.query = query
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake)

    request = JobRequest(verb="research", note_rel=NOTE, note_text="", skill="s",
                         vault=config.vault)
    with pytest.raises(AgentUnavailable, match="Connection reset"):
        ClaudeAgentRunner(config).run(request)


def test_an_answer_without_a_result_block_is_not_the_machine(config, monkeypatch) -> None:
    """The model ran and said something unusable. That is about the job."""
    fake = types.ModuleType("claude_agent_sdk")
    fake.ClaudeAgentOptions = lambda **kw: kw

    class Msg:
        content = [types.SimpleNamespace(text="I could not find anything.")]

    async def query(prompt, options):
        yield Msg()

    fake.query = query
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake)

    request = JobRequest(verb="research", note_rel=NOTE, note_text="", skill="s",
                         vault=config.vault)
    with pytest.raises(AgentError) as info:
        ClaudeAgentRunner(config).run(request)
    assert not is_machine(info.value)
