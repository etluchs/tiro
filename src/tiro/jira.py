"""Jira Cloud, through Atlassian's `acli` (DESIGN section 5.5).

`dispatch` is the only job whose effect git cannot revert, so this module is
written defensively at every step.

* **`acli` holds the credentials, not Tiro.** `acli jira auth login` is a setup
  step; nothing here ever reads, stores or passes a token.
* **The model drafts, this posts.** A payload arrives from the agent, is
  validated against a small schema we own, and is posted by a subprocess the
  agent cannot reach. Issue creation is not a tool the model holds.
* **Idempotency is ours to provide**, because Jira has no idempotency key on
  create: the note's frontmatter first, then a JQL search on the note's stable
  label, and only then a create.
* **Exit codes are always 0**, as with the Obsidian CLI, so nothing branches on
  returncode alone.

UNVERIFIED AGAINST A LIVE JIRA. The command shapes come from the published
`acli` reference and have not been run. They are in one place, `COMMANDS`, and
`tiro doctor` exercises the read-only ones.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from tiro.failure import MachineFailure

TIMEOUT = 60.0

#: Every field Tiro will send. A payload naming anything else is rejected rather
#: than passed through: `--from-json` is thinly documented, and the failure mode
#: of guessing is an issue created with the wrong shape that cannot be unmade.
ALLOWED_PAYLOAD_KEYS = {"summary", "description", "type", "labels"}

#: The label every Tiro-created issue carries, so that `labels = tiro` is both
#: the audit and the duplicate check. Issues are created under the user's own
#: account, so nothing else on the board distinguishes them.
PROVENANCE_LABEL = "tiro"


class JiraError(Exception):
    """Something went wrong reaching Jira. Always blocks the note; never retried
    blindly, because a retry is how duplicates are made."""


class JiraDown(JiraError, MachineFailure):
    """acli could not be reached, or Jira could not be asked. Nothing was
    created — every raise of this happens before or instead of a create — so
    retrying is safe: the label search runs first on the next attempt.

    A create that returned no key is deliberately *not* this: Jira did
    something, Tiro cannot say what, and a person should look."""


@dataclass
class Payload:
    summary: str
    description: str
    issue_type: str
    labels: list[str] = field(default_factory=list)

    def to_json(self, project: str) -> dict:
        return {
            "project": {"key": project},
            "summary": self.summary,
            "description": self.description,
            "type": self.issue_type,
            "labels": sorted(set(self.labels)),
        }


def build_payload(
    raw: dict | None,
    *,
    project: str,
    default_type: str,
    note_rel: str,
    note_id: str,
    run_id: str,
) -> Payload:
    """Validate the agent's draft and add the parts the runner owns.

    The project key is set here and only here. A payload that names one is
    refused outright rather than corrected: the agent has no business choosing
    which project the user's issues land in.
    """
    if not isinstance(raw, dict):
        raise JiraError("dispatch produced no payload")
    unknown = set(raw) - ALLOWED_PAYLOAD_KEYS
    if unknown:
        raise JiraError(f"payload has fields Tiro will not send: {sorted(unknown)}")

    summary = str(raw.get("summary", "")).strip()
    if not summary:
        raise JiraError("payload has no summary")
    if len(summary) > 240:
        raise JiraError(f"summary is {len(summary)} characters; keep it under 240")

    description = str(raw.get("description", "")).strip()
    if not description:
        raise JiraError("payload has no description")

    labels = [str(l).strip() for l in (raw.get("labels") or []) if str(l).strip()]
    if any(" " in l for l in labels):
        raise JiraError("Jira labels cannot contain spaces")

    # Provenance, since the issue is created under the user's own account and
    # nothing else on the board would say it was Tiro (DESIGN section 5.5).
    labels += [PROVENANCE_LABEL, note_label(note_id)]
    description += f"\n\n---\nFiled by Tiro from `{note_rel}` (run {run_id})."

    return Payload(
        summary=summary,
        description=description,
        issue_type=str(raw.get("type") or default_type),
        labels=labels,
    )


def note_label(note_id: str) -> str:
    """The idempotency key Jira does not give us."""
    return f"tiro-{note_id}"


@dataclass
class Acli:
    site: str
    exe: str | None = None

    COMMANDS = {
        "auth": ["jira", "auth", "status"],
        "search": ["jira", "workitem", "search", "--jql={jql}", "--json"],
        "create": ["jira", "workitem", "create", "--from-json={path}", "--json"],
    }

    def __post_init__(self) -> None:
        self.exe = self.exe or shutil.which("acli")

    @property
    def installed(self) -> bool:
        return bool(self.exe)

    def _run(self, command: str, **params: str) -> tuple[str, str]:
        if not self.exe:
            raise JiraDown("acli is not installed; it belongs in the dev image")
        args = [part.format(**params) for part in self.COMMANDS[command]]
        try:
            done = subprocess.run(
                [self.exe, *args], capture_output=True, text=True, timeout=TIMEOUT
            )
        except subprocess.TimeoutExpired as exc:
            raise JiraDown(f"acli {command} timed out after {TIMEOUT:g}s") from exc
        return done.stdout or "", done.stderr or ""

    def authenticated(self) -> tuple[bool, str]:
        if not self.exe:
            return False, "acli is not installed"
        out, err = self._run("auth")
        text = (out + err).strip()
        if not text:
            return False, "acli jira auth status said nothing"
        looks_out = any(w in text.lower() for w in ("not logged", "no active", "unauthenticated"))
        return (not looks_out), text.splitlines()[0][:200]

    def search(self, jql: str) -> list[str]:
        """Issue keys matching a JQL query. An unparseable answer is an error,
        not an empty result — treating it as empty is how a duplicate is made."""
        out, err = self._run("search", jql=jql)
        if not out.strip():
            if err.strip():
                raise JiraDown(f"acli search failed: {err.strip().splitlines()[0][:200]}")
            return []
        try:
            data = json.loads(out)
        except json.JSONDecodeError as exc:
            raise JiraDown(f"acli search returned output we cannot read: {exc}") from exc
        return _keys(data)

    def create(self, payload: Payload, *, project: str, workdir: Path) -> str:
        """Create one issue and return its key.

        The key is read from `--json`, and if the output does not yield one we
        raise rather than guess. The caller then finds the issue by its label on
        the next run, which is exactly the crash-window path.
        """
        workdir.mkdir(parents=True, exist_ok=True)
        path = workdir / "payload.json"
        path.write_text(json.dumps(payload.to_json(project), indent=2), encoding="utf-8")
        out, err = self._run("create", path=str(path))
        keys = []
        try:
            keys = _keys(json.loads(out)) if out.strip() else []
        except json.JSONDecodeError:
            keys = []
        if not keys:
            raise JiraError(
                "acli create did not return an issue key; "
                f"output: {(out or err).strip()[:300]}"
            )
        return keys[0]


def _keys(payload: object) -> list[str]:
    """Issue keys out of whatever shape acli hands back."""
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            key = node.get("key")
            if isinstance(key, str) and "-" in key:
                found.append(key)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return found
