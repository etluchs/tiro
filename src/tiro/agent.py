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
from tiro.failure import MachineFailure

_JSON_FENCE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)

#: Tiro's tool surface, and the only place it is defined.
#:
#: This is what DESIGN section 3.3 means by "the tool allowlist". It is passed
#: to the SDK per run; it is *not* read from a settings file, and in particular
#: not from this repo's ``.claude/settings.json``, which configures Claude Code
#: sessions a human opens here and has no bearing on what Tiro can do.
#:
#: **Bash is denied whole, not per command.** The design once described this as
#: denying ``Bash(git *)``, ``Bash(obsidian *)`` and ``Bash(acli *)`` — the three
#: vendor tools that are the runner's and not the agent's. Denying the tool
#: itself is the stronger property and the easier one to verify: there is no
#: pattern to slip past with an absolute path, an alias, or a shell that spawns
#: another. The runner still calls all three; the agent simply has no way to run
#: a command at all.
ALLOWED_TOOLS = ("Read", "Glob", "Grep", "WebSearch", "WebFetch")
DENIED_TOOLS = ("Write", "Edit", "NotebookEdit", "Bash")


class AgentError(Exception):
    """The agent could not be run, or did not answer in the required shape."""


class AgentUnavailable(AgentError, MachineFailure):
    """The agent could not be run at all — the SDK missing, the network down,
    the model unreachable. Distinct from an agent that ran and could not do the
    job, which is about the material and waits for the user."""


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
class Usage:
    """What a job cost. Kept per job so the per-day ceiling has real numbers to
    be set from (DESIGN section 5.4 and open question 3).

    ``cost_usd`` is ``None`` rather than ``0.0`` when the SDK did not report a
    cost — on a Claude subscription it often does not — because "free" and
    "unknown" are different facts and a budget built on the wrong one is worse
    than no budget.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float | None = None

    @property
    def total_tokens(self) -> int:
        return (self.input_tokens + self.output_tokens
                + self.cache_read_tokens + self.cache_write_tokens)

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens
        if other.cost_usd is not None:
            self.cost_usd = (self.cost_usd or 0.0) + other.cost_usd

    @classmethod
    def from_sdk(cls, raw: dict | None) -> "Usage":
        """Read the SDK's ``usage``, which is a **dict**, not an object.

        It was read with ``getattr`` once, which silently returned the default
        for every field and reported zero tokens on every run.
        """
        raw = raw or {}

        def n(*names: str) -> int:
            for name in names:
                value = raw.get(name)
                if value is not None:
                    return int(value)
            return 0

        return cls(
            input_tokens=n("input_tokens"),
            output_tokens=n("output_tokens"),
            cache_read_tokens=n("cache_read_input_tokens", "cache_read_tokens"),
            cache_write_tokens=n("cache_creation_input_tokens", "cache_creation_tokens"),
        )


@dataclass
class JobOutput:
    """What a job produced. The runner decides what to do with it."""

    status: str = "done"  # done | needs-input | blocked
    block: str = ""
    keys: dict[str, str] = field(default_factory=dict)
    payload: dict | None = None
    detail: str = ""
    usage: Usage = field(default_factory=Usage)

    @classmethod
    def from_json(cls, data: dict) -> "JobOutput":
        status = str(data.get("status", "done"))
        if status not in ("done", "needs-input", "blocked"):
            raise AgentError(f"agent returned an invalid status: {status!r}")
        keys = {}
        for key, value in (data.get("keys") or {}).items():
            if str(key) == "tiro":
                # The verb is the user's key. An agent that could set it could
                # queue its own next job — spec into dispatch, say — and the
                # protocol's "only the user releases it" would be prompt-deep.
                raise AgentError("agent tried to set `tiro`, which only the user writes")
            if not str(key).startswith("tiro/"):
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


def agent_options(request: JobRequest, *, root: Path) -> dict:
    """Every option the SDK run is given, as a plain dict.

    Separated from the SDK call so the safety-relevant half can be asserted in a
    test without the SDK installed and without spending a token. ``test_agent.py``
    is that test, and it is the one ITERATION-1 M6 asks for.
    """
    return {
        "model": request.model,
        "cwd": str(request.vault),
        "allowed_tools": list(ALLOWED_TOOLS),
        "disallowed_tools": list(DENIED_TOOLS),
        # There is nobody to prompt on a timer, so anything not pre-approved is
        # refused rather than asked about.
        "permission_mode": "dontAsk",
        # No settings file is read at all: not the vault's, so a note cannot
        # widen the tool surface, and not this repo's.
        "setting_sources": [],
        "max_turns": request.max_turns,
        "system_prompt": {"type": "file", "path": str(root / "CLAUDE.md")},
    }


class ClaudeAgentRunner:
    """The real thing, on the Claude Agent SDK.

    Read-only tools only, from ``agent_options`` above.
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
            raise AgentUnavailable(
                "claude-agent-sdk is not installed; pip install 'tiro[agent]'"
            ) from exc

        options = ClaudeAgentOptions(**agent_options(request, root=self.config.root))
        chunks: list[str] = []
        per_turn = Usage()  # summed from each AssistantMessage
        final: Usage | None = None  # the ResultMessage's own totals, if any
        cost: float | None = None

        try:
            async for message in query(prompt=request.skill, options=options):
                for block in getattr(message, "content", []) or []:
                    text = getattr(block, "text", None)
                    if text:
                        chunks.append(text)
                raw = getattr(message, "usage", None)
                # Only ResultMessage has total_cost_usd, so it is how we tell the
                # run's totals from one turn's — adding both would double-count.
                if hasattr(message, "total_cost_usd"):
                    if raw:
                        final = Usage.from_sdk(raw)
                    if message.total_cost_usd is not None:
                        cost = float(message.total_cost_usd)
                elif raw:
                    per_turn.add(Usage.from_sdk(raw))
        except AgentError:
            raise
        except Exception as exc:  # noqa: BLE001 - the SDK's failures are many
            # The model could not be reached or the SDK fell over: the network,
            # a rate limit, an expired login, the CLI process dying. None of
            # that is about the note, so the note retries by itself.
            raise AgentUnavailable(
                f"the agent could not run: {type(exc).__name__}: {exc}"
            ) from exc

        output = parse_result("\n".join(chunks))
        output.usage = final or per_turn
        output.usage.cost_usd = cost
        return output
