"""The agent's tool surface.

ITERATION-1 M6 asks for a test that the three vendor CLIs are denied to the
agent. This is it, and it asserts the stronger thing the code actually does:
the agent holds no command-running tool at all, so there is no `Bash(acli *)`
pattern to get past.

None of this needs the SDK installed or a token spent, because `agent_options`
returns a plain dict and the assertions are about that dict.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tiro import agent as agent_mod
from tiro.agent import ALLOWED_TOOLS, DENIED_TOOLS, JobRequest, agent_options

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def options(vault: Path) -> dict:
    return agent_options(
        JobRequest(verb="research", note_rel="n.md", note_text="", skill="", vault=vault),
        root=REPO,
    )


def test_the_agent_cannot_run_a_command_at_all(options: dict) -> None:
    """git, obsidian and acli are the runner's, not the agent's. Denying Bash
    itself is how that holds, rather than three command patterns."""
    assert "Bash" in options["disallowed_tools"]
    assert not any(t.startswith("Bash") for t in options["allowed_tools"])


def test_the_agent_cannot_write_to_the_vault(options: dict) -> None:
    """Constitution rule 3 is a tool the model does not have, not a rule it has
    to remember."""
    for tool in ("Write", "Edit", "NotebookEdit"):
        assert tool in options["disallowed_tools"]
    assert set(options["allowed_tools"]) == set(ALLOWED_TOOLS)
    assert not set(ALLOWED_TOOLS) & set(DENIED_TOOLS)


def test_no_settings_file_can_widen_the_tool_surface(options: dict) -> None:
    """A note is data. So is a settings file sitting in the vault — and so is
    this repo's own .claude/settings.json, which configures Claude Code
    sessions a human opens here and never reaches Tiro."""
    assert options["setting_sources"] == []


def test_nothing_is_asked_because_nobody_is_there(options: dict) -> None:
    assert options["permission_mode"] == "dontAsk"


def test_the_constitution_is_the_system_prompt(options: dict) -> None:
    assert options["system_prompt"] == {
        "type": "file", "path": str(REPO / "CLAUDE.md"),
    }
    assert (REPO / "CLAUDE.md").exists()


def test_the_agent_is_pointed_at_the_vault_and_bounded(options: dict, vault: Path) -> None:
    assert options["cwd"] == str(vault)
    assert options["max_turns"] > 0


def test_the_tool_surface_is_defined_in_one_place() -> None:
    """If the lists get inlined at the SDK call again, this test is the thing
    that notices — the docs point here, so here is where it must stay."""
    source = (REPO / "src" / "tiro" / "agent.py").read_text(encoding="utf-8")
    assert source.count('"Read", "Glob", "Grep"') == 1
    assert "ClaudeAgentOptions(**agent_options(" in source


def test_this_repos_settings_file_is_not_tiros_policy() -> None:
    """The reconciliation this test file exists to pin down. `.claude/settings.json`
    is read by Claude Code, not by Tiro; whatever it denies, Tiro's surface is
    the one above."""
    import json

    settings = json.loads((REPO / ".claude" / "settings.json").read_text(encoding="utf-8"))
    denied_there = set(settings.get("permissions", {}).get("deny", []))
    assert not denied_there & set(DENIED_TOOLS)  # different vocabulary entirely
    assert agent_mod.ALLOWED_TOOLS  # and Tiro's own surface stands on its own
