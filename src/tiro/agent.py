"""The agent layer.

**The agent is read-only on the vault.** It reads, searches, thinks, and returns
a structured result; the *runner* serialises that into frontmatter keys and
blocks and hands it to the gate. Nothing in the vault changes as a side effect
of a tool call.

That is a bigger decision than it looks. It means the agent never holds Write or
Edit, so rule 3 of the constitution ("never edit the user's prose") is not a rule
the model has to remember — it is a tool it does not have. It means Tiro owns the
exact bytes of its own serialisation, so blocks and keys are uniform and diffs
are clean. And it means the whole loop is testable without a model: swap in
``ScriptedAgent`` and the gate, the commits and the journal are all still real.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from tiro.config import Config

_JSON_FENCE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


class AgentError(Exception):
    """The agent could not be run, or did not answer in the required shape."""


@dataclass(frozen=True)
class JobRequest:
    verb: str
    note_rel: str
    note_text: str
    skill: str
    vault: Path
    max_turns: int = 30
    model: str = "claude-opus-5"
    effort: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class JobOutput:
    """What a job produced. The runner decides what to do with it."""

    status: str = "done"  # done | needs-input | blocked
    block: str = ""
    keys: dict[str, str] = field(default_factory=dict)
    payload: dict | None = None
    detail: str = ""
    cost_usd: float = 0.0
    tokens: int = 0

    @classmethod
    def from_json(cls, data: dict) -> "JobOutput":
        status = str(data.get("status", "done"))
        if status not in ("done", "needs-input", "blocked"):
            raise AgentError(f"agent returned an invalid status: {status!r}")
        keys = {}
        for key, value in (data.get("keys") or {}).items():
            if key != "tiro" and not str(key).startswith("tiro/"):
                raise AgentError(f"agent tried to set a non-Tiro key: {key!r}")
            keys[str(key)] = str(value)
        return cls(
            status=status,
            block=str(data.get("block", "")),
            keys=keys,
            payload=data.get("payload"),
            detail=str(data.get("detail", ""))[:200],
        )


class AgentRunner(Protocol):
    name: str

    def run(self, request: JobRequest) -> JobOutput: ...


def parse_result(text: str) -> JobOutput:
    """Pull the result object out of the agent's final message.

    The last fenced ``json`` block wins, so the agent may think out loud first.
    An answer we cannot parse is an error, never a guess: a job whose result we
    had to interpret is a job we cannot claim to have verified.
    """
    matches = _JSON_FENCE.findall(text or "")
    if not matches:
        raise AgentError("agent produced no ```json result block")
    try:
        data = json.loads(matches[-1])
    except json.JSONDecodeError as exc:
        raise AgentError(f"agent's result block is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise AgentError("agent's result block is not an object")
    return JobOutput.from_json(data)


class ScriptedAgent:
    """A stand-in for tests: canned results by verb, no model, no network."""

    name = "scripted"

    def __init__(self, results: dict[str, JobOutput | Exception]) -> None:
        self.results = results
        self.calls: list[JobRequest] = []

    def run(self, request: JobRequest) -> JobOutput:
        self.calls.append(request)
        result = self.results.get(request.verb)
        if result is None:
            raise AgentError(f"no scripted result for {request.verb!r}")
        if isinstance(result, Exception):
            raise result
        return result


class ClaudeAgentRunner:
    """The real thing, on the Claude Agent SDK.

    Read-only tools only. ``permission_mode=\"dontAsk\"`` because there is nobody
    to ask, and ``setting_sources=[]`` so nothing inside the vault can widen the
    tool surface.
    """

    name = "claude-agent-sdk"

    def __init__(self, config: Config) -> None:
        self.config = config

    def run(self, request: JobRequest) -> JobOutput:
        import asyncio

        return asyncio.run(self._run(request))

    async def _run(self, request: JobRequest) -> JobOutput:
        try:
            from claude_agent_sdk import ClaudeAgentOptions, query
        except ImportError as exc:  # pragma: no cover - depends on the extra
            raise AgentError(
                "claude-agent-sdk is not installed; pip install 'tiro[agent]'"
            ) from exc

        options = ClaudeAgentOptions(
            model=request.model,
            cwd=str(request.vault),
            allowed_tools=["Read", "Glob", "Grep", "WebSearch", "WebFetch"],
            disallowed_tools=["Write", "Edit", "NotebookEdit", "Bash"],
            permission_mode="dontAsk",
            setting_sources=[],
            max_turns=request.max_turns,
            system_prompt={"type": "file", "path": str(self.config.root / "CLAUDE.md")},
        )
        chunks: list[str] = []
        cost = 0.0
        tokens = 0
        async for message in query(prompt=request.skill, options=options):
            for block in getattr(message, "content", []) or []:
                text = getattr(block, "text", None)
                if text:
                    chunks.append(text)
            usage = getattr(message, "usage", None)
            if usage:
                tokens += int(getattr(usage, "output_tokens", 0) or 0)
            total = getattr(message, "total_cost_usd", None)
            if total:
                cost = float(total)

        output = parse_result("\n".join(chunks))
        output.cost_usd, output.tokens = cost, tokens
        return output
