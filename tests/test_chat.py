"""`tiro chat`, `tiro ask`, and `tiro once --note`.

Chat itself is an interactive Claude Code session, which a test cannot hold a
conversation with. What can be tested is everything that makes it safe: the
exact session it would start, the guard every Bash call passes through, and the
two commands chat uses to get anything done.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from tiro import chat, cli, protocol, runner
from tiro.agent import JobOutput, ScriptedAgent
from tiro.config import IndexConfig, TrustMap
from tiro.ops_fs import FilesystemOps

SRC = Path(__file__).resolve().parents[1] / "src"


# --- the guard ------------------------------------------------------------


@pytest.mark.parametrize("command", [
    "tiro status",
    "tiro lint",
    "tiro --help",
    'tiro ask "00 Inbox/a.md" "research this (briefly), please"',
    'tiro once --note "00 Inbox/a.md"',
    "tiro --vault /v --backend fs once --dry-run",
    "tiro accept R-004",
    'tiro reject R-004 "not a pattern"',
    "tiro undo 2026-10-08T07-00Z-ab12",
])
def test_one_tiro_command_may_run(command: str) -> None:
    assert chat.check(command) is None


@pytest.mark.parametrize("command, why", [
    ("ls Tiro", "nothing else"),
    ("rm -rf .", "nothing else"),
    ("git reset --hard", "nothing else"),
    ("/usr/local/bin/tiro status", "nothing else"),
    ("tiro status; rm -rf .", "one plain"),
    ("tiro status && rm x", "one plain"),
    ("tiro status | sh", "one plain"),
    ("tiro status > Areas/x.md", "one plain"),
    ("tiro ask a $(cat secret)", "one plain"),
    ("tiro ask a `id`", "one plain"),
    ("tiro status\nrm x", "one plain"),
    ('tiro ask "a" "un\\"closed', "one plain"),
    ("tiro chat", "not available"),
    ("tiro --root /elsewhere status", "name a `tiro` subcommand"),
    ("tiro", "name a `tiro` subcommand"),
    ('tiro ask "unclosed', "does not parse"),
])
def test_everything_else_is_refused(command: str, why: str) -> None:
    assert why in (chat.check(command) or "")


def test_the_guard_only_judges_bash() -> None:
    assert chat.guard({"tool_name": "Read", "tool_input": {"file_path": "/etc/passwd"}}) == (0, "")
    code, message = chat.guard({"tool_name": "Bash", "tool_input": {"command": "ls"}})
    assert code == 2 and "Refused by Tiro's chat guard" in message


def test_the_hook_runs_as_claude_code_runs_it() -> None:
    """The command the settings name, with the event on stdin, as a separate
    process: exit 2 and a reason on stderr blocks the call."""
    def hook(command: str) -> subprocess.CompletedProcess:
        event = {"tool_name": "Bash", "tool_input": {"command": command}}
        return subprocess.run([sys.executable, "-m", "tiro.chat"], input=json.dumps(event),
                              capture_output=True, text=True,
                              env={**os.environ, "PYTHONPATH": str(SRC)})

    blocked = hook("cat ~/.ssh/id_rsa")
    assert blocked.returncode == 2 and "nothing else" in blocked.stderr
    assert hook("tiro status").returncode == 0


def test_unreadable_hook_input_blocks(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert chat.main() == 2


# --- the session it starts ------------------------------------------------


def _launch(config, **kw):
    return chat.launch(config, claude="/opt/bin/claude", python="/venv/bin/python",
                       need_tiro=False, **kw)


def _flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


def test_the_session_has_no_tool_that_writes(config) -> None:
    argv = _launch(config).argv
    tools = _flag(argv, "--tools").split(",")
    assert set(tools) == {"Read", "Glob", "Grep", "WebSearch", "WebFetch", "Bash"}
    deny = json.loads(_flag(argv, "--settings"))["permissions"]["deny"]
    assert {"Write", "Edit", "NotebookEdit"} <= set(deny)


def test_the_session_is_configured_here_not_by_the_vault(config, vault) -> None:
    spec = _launch(config)
    argv = spec.argv
    assert spec.cwd == config.root  # so the constitution, CLAUDE.md, is loaded
    assert _flag(argv, "--add-dir") == str(vault)
    assert _flag(argv, "--setting-sources") == "user"  # not this repo's, not the vault's
    assert "--strict-mcp-config" in argv
    assert _flag(argv, "--model") == config.agent.model


def test_every_bash_call_passes_the_guard_and_only_reads_skip_the_prompt(config) -> None:
    conf = json.loads(_flag(_launch(config).argv, "--settings"))
    [entry] = conf["hooks"]["PreToolUse"]
    assert entry["matcher"] == "Bash"
    assert entry["hooks"][0]["command"] == "/venv/bin/python -m tiro.chat"
    bash = sorted(a for a in conf["permissions"]["allow"] if a.startswith("Bash("))
    assert bash == ["Bash(tiro auto status)", "Bash(tiro doctor)", "Bash(tiro lint)",
                    "Bash(tiro once --dry-run)", "Bash(tiro status)"]


def test_a_vault_override_is_spelled_out_to_chat(config, vault) -> None:
    argv = _launch(config, vault_override=str(vault)).argv
    tiro = f"tiro --vault {vault}"
    assert f"{tiro} ask" in _flag(argv, "--append-system-prompt")
    allow = json.loads(_flag(argv, "--settings"))["permissions"]["allow"]
    assert f"Bash({tiro} status)" in allow
    assert chat.check(f"{tiro} status") is None


def test_the_prompt_says_where_things_are_and_how_to_act(config, vault) -> None:
    text = _flag(_launch(config).argv, "--append-system-prompt")
    for needle in (str(vault), "Tiro/Journal/", "Tiro/Questions.md", "tiro ask",
                   "tiro once --note", "tool that writes a file"):
        assert needle in text


def test_an_opening_message_is_passed_on(config) -> None:
    assert _launch(config, first="what did you do last night?").argv[-1] == \
        "what did you do last night?"


def test_without_tiro_on_path_chat_does_not_start(config, monkeypatch) -> None:
    """Chat acts only through `tiro`; started without it, every command it ran
    would fail with 127 (found by running it, 2026-10-08)."""
    monkeypatch.setattr(chat.shutil, "which", lambda name, path=None: None)
    monkeypatch.setenv("PATH", "/nowhere")
    with pytest.raises(FileNotFoundError, match="`tiro` command is not on PATH"):
        chat.launch(config, claude="/opt/bin/claude", python="/venv/bin/python")


def test_without_claude_code_it_says_how_to_get_it(config, monkeypatch, capsys) -> None:
    monkeypatch.setattr(chat.shutil, "which", lambda _: None)
    args = ["--root", str(config.root), "--vault", str(config.vault), "chat"]
    assert cli.main(args) == 1
    assert "Claude Code is not installed" in capsys.readouterr().out


# --- tiro ask -------------------------------------------------------------


def _ask(config, *args) -> int:
    return cli.main(["--root", str(config.root), "--vault", str(config.vault), "ask", *args])


def test_ask_puts_the_request_where_obsidian_shows_it(config, vault, capsys) -> None:
    before = (vault / "Areas/orphan.md").read_text()
    assert _ask(config, "Areas/orphan", "research") == 0
    after = (vault / "Areas/orphan.md").read_text()
    assert protocol.read_keys(after)["tiro"] == "research"
    assert after.endswith(before)  # the user's text untouched, below the new frontmatter
    assert 'tiro once --note "Areas/orphan.md"' in capsys.readouterr().out


def test_a_sentence_is_quoted_when_yaml_needs_it(config, vault) -> None:
    assert _ask(config, "Areas/orphan.md", "summarise: three bullets") == 0
    text = (vault / "Areas/orphan.md").read_text()
    assert 'tiro: "summarise: three bullets"' in text
    assert protocol.verb(text) == protocol.ORDER
    assert protocol.order(text) == "summarise: three bullets"


def test_ask_does_not_overwrite_another_request_unless_told(config, vault, capsys) -> None:
    note = "00 Inbox/bitter-lesson.md"  # asks for research already
    assert _ask(config, note, "distill") == 1
    assert "already asks for `research`" in capsys.readouterr().out
    assert protocol.read_keys((vault / note).read_text())["tiro"] == "research"
    assert _ask(config, note, "distill", "--replace") == 0
    assert protocol.read_keys((vault / note).read_text())["tiro"] == "distill"


@pytest.mark.parametrize("note, words, why", [
    ("Areas/orphan.md", "reserch", "is not a verb"),
    ("Areas/orphan.md", "   ", "say what you want"),
    ("Areas/nowhere.md", "research", "no such note"),
    ("../outside.md", "research", "not in the vault"),
    ("Tiro/Journal/x.md", "research", "not a note Tiro takes requests on"),
    ("Private/diary.md", "research", "L0"),
])
def test_ask_refuses_what_it_should(config, vault, capsys, note, words, why) -> None:
    (vault / "Tiro/Journal").mkdir(parents=True, exist_ok=True)
    (vault / "Tiro/Journal/x.md").write_text("journal\n", encoding="utf-8")
    (vault / "Private").mkdir(exist_ok=True)
    (vault / "Private/diary.md").write_text("secret\n", encoding="utf-8")
    (vault.parent / "outside.md").write_text("outside\n", encoding="utf-8")
    assert _ask(config, note, words) == 1
    assert why in capsys.readouterr().out


# --- tiro once --note -----------------------------------------------------


def _agent():
    return ScriptedAgent({"research": JobOutput(block="> [!abstract] now"),
                          "triage": JobOutput(block="y"), "order": JobOutput(block="o")})


def test_a_named_run_does_that_note_now_and_nothing_else(config, vault) -> None:
    # Both notes were touched a moment ago; the timer would wait for both.
    (vault / "Areas/now.md").write_text("---\ntiro: research\n---\n\nNow, please.\n",
                                        encoding="utf-8")
    record = runner.once(config, agent=_agent(), ops=FilesystemOps(vault),
                         only={"Areas/now.md"})
    assert [e.note for e in record.entries] == ["Areas/now.md"]
    assert "now" in (vault / "Areas/now.md").read_text()
    # The fixture's other tagged notes were not touched.
    assert "tiro/status" not in protocol.read_keys((vault / "00 Inbox/bitter-lesson.md").read_text())


def test_a_named_run_leaves_the_timers_work_to_the_timer(config, vault) -> None:
    trust = vault / ".tiro/trust.toml"
    trust.write_text(trust.read_text() + '"Areas/" = "L3"\n', encoding="utf-8")
    config = replace(config, trust=TrustMap.load(trust), index=IndexConfig(folders=("Areas",)))
    (vault / "Areas/now.md").write_text("---\ntiro: research\n---\n\nbody\n", encoding="utf-8")

    record = runner.once(config, agent=_agent(), ops=FilesystemOps(vault), only={"Areas/now.md"})
    assert not (vault / "Areas/Areas — Index.md").exists()
    assert not any("index" in n or "proposes" in n for n in record.notes)
    assert "reflect" not in runner._load_state(config)


def test_once_names_notes_from_the_command_line(config, vault, capsys, monkeypatch) -> None:
    calls = {}

    def fake_once(config, *, agent, ops, only=None, **kw):
        calls["only"] = only
        return runner.journal.RunRecord(run_id="r", started="s", ops_backend=ops.name)

    monkeypatch.setattr(runner, "once", fake_once)
    monkeypatch.setattr("tiro.agent.ClaudeAgentRunner", lambda config: None)
    args = ["--root", str(config.root), "--vault", str(vault), "--backend", "fs", "once"]
    assert cli.main([*args, "--note", "Areas/orphan"]) == 0
    assert calls["only"] == {"Areas/orphan.md"}
    assert cli.main([*args, "--note", "Areas/nowhere"]) == 1
    assert "no such note" in capsys.readouterr().out


def test_ask_then_once_is_the_whole_round_trip(config, vault) -> None:
    assert _ask(config, "Areas/orphan.md", "find what links here and say why") == 0
    record = runner.once(config, agent=_agent(), ops=FilesystemOps(vault),
                         only={"Areas/orphan.md"})
    [entry] = record.entries
    assert (entry.verb, entry.outcome) == ("order", "done")
    text = (vault / "Areas/orphan.md").read_text()
    assert protocol.read_keys(text)["tiro/status"] == "done"
