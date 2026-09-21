"""dispatch, against a stub acli.

This is the only job whose effect git cannot revert, so the tests are about
what happens when things go wrong rather than when they go right: a crash
between create and write-back, an unreadable search, a payload that names a
project it has no business naming.

The stub records every invocation, so "did not create a second issue" is an
assertion about calls made, not about the absence of an error.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from tiro import runner
from tiro.agent import JobOutput, ScriptedAgent
from tiro.config import Config, DispatchConfig
from tiro.jira import JiraError, build_payload
from tiro.ops_fs import FilesystemOps

STUB = '''#!/usr/bin/env python3
import json, os, sys
control = json.loads(open(os.environ["ACLI_STUB"]).read())
control.setdefault("calls", []).append(sys.argv[1:])
json.dump(control, open(os.environ["ACLI_STUB"], "w"))
argv = " ".join(sys.argv[1:])
if "auth" in argv:
    print("Logged in as someone@example.invalid")
elif "search" in argv:
    if control.get("search_raises"):
        sys.stderr.write("boom\\n")
    else:
        print(json.dumps([{"key": k} for k in control.get("search_result", [])]))
elif "create" in argv:
    if control.get("create_silent"):
        sys.exit(0)
    print(json.dumps({"key": control.get("create_key", "TIRO-1")}))
'''


@pytest.fixture
def acli(tmp_path: Path, monkeypatch):
    """A stub `acli` on PATH, plus its control file."""
    exe = tmp_path / "acli"
    exe.write_text(STUB, encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    control = tmp_path / "acli-control.json"
    control.write_text(json.dumps({"calls": []}), encoding="utf-8")
    monkeypatch.setenv("ACLI_STUB", str(control))
    monkeypatch.setattr("shutil.which", lambda name: str(exe) if name == "acli" else None)

    class Stub:
        path = control

        @property
        def calls(self) -> list[list[str]]:
            return json.loads(control.read_text())["calls"]

        def set(self, **kw) -> None:
            data = json.loads(control.read_text())
            data.update(kw)
            control.write_text(json.dumps(data), encoding="utf-8")

        def creates(self) -> list[list[str]]:
            # Match the subcommand, not the string: a JQL "ORDER BY created"
            # contains the word too.
            return [c for c in self.calls if c[:3] == ["jira", "workitem", "create"]]

    return Stub()


def _configured(config: Config, *, live: bool) -> Config:
    from dataclasses import replace

    return replace(config, dispatch=DispatchConfig(
        site="example.atlassian.net", project="TIRO", issue_type="Task", live=live))


def _live(config: Config) -> Config:
    return _configured(config, live=True)


def _preview(config: Config) -> Config:
    return _configured(config, live=False)


def _spec_note(vault: Path, name: str = "00 Inbox/thing.md") -> Path:
    path = vault / name
    path.write_text("---\ntiro: dispatch\n---\n\nA spec the user signed off.\n", encoding="utf-8")
    os.utime(path, (0, 0))
    return path


def _payload(**kw) -> dict:
    return {"summary": "Do the thing", "description": "Problem\n\n1. criterion", **kw}


def _run(config, stub_output: JobOutput, vault: Path):
    for p in vault.rglob("*.md"):
        os.utime(p, (0, 0))
    agent = ScriptedAgent({
        "dispatch": stub_output,
        "research": JobOutput(block="x"),
        "triage": JobOutput(block="y"),
    })
    return runner.once(config, agent=agent, ops=FilesystemOps(config.vault))


# --- the payload the runner will send ------------------------------------


def test_the_runner_sets_the_project_not_the_agent() -> None:
    payload = build_payload(_payload(), project="TIRO", default_type="Task",
                            note_rel="a.md", note_id="abc", run_id="r1")
    assert payload.to_json("TIRO")["project"] == {"key": "TIRO"}


def test_a_payload_naming_a_project_is_refused() -> None:
    with pytest.raises(JiraError, match="will not send"):
        build_payload(_payload(project="OTHER"), project="TIRO", default_type="Task",
                      note_rel="a.md", note_id="abc", run_id="r1")


def test_every_issue_carries_provenance_labels() -> None:
    payload = build_payload(_payload(), project="TIRO", default_type="Task",
                            note_rel="00 Inbox/a.md", note_id="abc", run_id="r1")
    assert "tiro" in payload.labels
    assert "tiro-abc" in payload.labels
    assert "00 Inbox/a.md" in payload.description


def test_an_empty_summary_is_refused() -> None:
    with pytest.raises(JiraError, match="no summary"):
        build_payload({"summary": "   ", "description": "d"}, project="TIRO",
                      default_type="Task", note_rel="a.md", note_id="abc", run_id="r1")


# --- preview is the default ----------------------------------------------


def test_preview_creates_nothing_and_shows_the_payload(config, vault: Path, acli) -> None:
    note = _spec_note(vault)
    record = _run(_preview(config), JobOutput(payload=_payload(), block="> [!abstract] Tiro"), vault)

    entry = next(e for e in record.entries if e.note.endswith("thing.md"))
    assert entry.outcome == "preview"
    assert acli.creates() == []
    assert '"summary": "Do the thing"' in note.read_text()
    assert "nothing has been created" in note.read_text()


# --- live, and exactly once ----------------------------------------------


def test_live_dispatch_creates_one_issue_and_records_the_key(config, vault: Path, acli) -> None:
    note = _spec_note(vault)
    cfg = _live(config)
    acli.set(create_key="TIRO-42")

    record = _run(cfg, JobOutput(payload=_payload(), block="> [!abstract] Tiro"), vault)

    assert len(acli.creates()) == 1
    assert "tiro/jira: TIRO-42" in note.read_text()
    entry = next(e for e in record.entries if e.note.endswith("thing.md"))
    assert "TIRO-42" in entry.detail


def test_a_note_already_filed_is_never_filed_again(config, vault: Path, acli) -> None:
    note = _spec_note(vault)
    note.write_text(
        "---\ntiro: dispatch\ntiro/jira: TIRO-7\n---\n\nA spec.\n", encoding="utf-8")
    cfg = _live(config)

    _run(cfg, JobOutput(payload=_payload(), block="x"), vault)

    assert acli.creates() == []
    assert "TIRO-7" in note.read_text()


def test_the_crash_window_adopts_rather_than_duplicating(config, vault: Path, acli) -> None:
    """The issue was created but the write-back died. The label search finds it."""
    _spec_note(vault)
    cfg = _live(config)
    acli.set(search_result=["TIRO-9"])

    _run(cfg, JobOutput(payload=_payload(), block="x"), vault)

    assert acli.creates() == []
    assert "tiro/jira: TIRO-9" in (vault / "00 Inbox/thing.md").read_text()


def test_an_unreadable_search_stops_rather_than_creating(config, vault: Path, acli) -> None:
    """Treating a failed search as 'no results' is exactly how a duplicate is
    made, so it blocks instead."""
    note = _spec_note(vault)
    cfg = _live(config)
    acli.set(search_raises=True)

    record = _run(cfg, JobOutput(payload=_payload(), block="x"), vault)

    assert acli.creates() == []
    entry = next(e for e in record.entries if e.note.endswith("thing.md"))
    assert entry.outcome == "blocked"
    assert "tiro/jira" not in note.read_text()


def test_a_create_that_returns_no_key_blocks_loudly(config, vault: Path, acli) -> None:
    note = _spec_note(vault)
    cfg = _live(config)
    acli.set(create_silent=True)

    record = _run(cfg, JobOutput(payload=_payload(), block="x"), vault)

    entry = next(e for e in record.entries if e.note.endswith("thing.md"))
    assert entry.outcome == "blocked"
    assert "did not return an issue key" in entry.detail
    assert "tiro/jira" not in note.read_text()


def test_a_spec_the_agent_is_unsure_about_files_nothing(config, vault: Path, acli) -> None:
    _spec_note(vault)
    cfg = _live(config)

    record = _run(cfg, JobOutput(status="needs-input", block="> [!question] Which project?"), vault)

    assert acli.creates() == []
    entry = next(e for e in record.entries if e.note.endswith("thing.md"))
    assert entry.outcome == "needs-input"


def test_dispatch_without_a_configured_project_refuses(config, vault: Path, acli) -> None:
    from dataclasses import replace

    _spec_note(vault)
    cfg = replace(config, dispatch=DispatchConfig(site="x", project="", live=True))

    record = _run(cfg, JobOutput(payload=_payload(), block="x"), vault)

    assert acli.creates() == []
    entry = next(e for e in record.entries if e.note.endswith("thing.md"))
    assert entry.outcome == "blocked"
    assert "refusing to guess" in entry.detail
