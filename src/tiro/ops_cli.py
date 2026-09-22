"""Obsidian CLI backend.

UNVERIFIED AGAINST A LIVE OBSIDIAN. Every command string below is written from
the published reference (https://obsidian.md/help/cli) and has not been run
against the real thing, because the machine this was built on has no Obsidian.
``tiro doctor`` exercises each one and reports exactly which call failed, so
confirming the surface is one command rather than an afternoon.

Two rules from the design apply to every call here (DESIGN section 3.4):

* **Exit codes are always 0**, even on failure — so never branch on returncode
  alone; parse the output, and verify the effect afterwards.
* **About a second per command**, with no batching — so use the vault-wide
  queries, never a per-note loop.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from tiro.ops import BrokenLink, OpsUnavailable

TIMEOUT = 30.0


@dataclass
class CliResult:
    ok: bool
    out: str
    err: str
    parsed: object | None


class ObsidianCliOps:
    name = "obsidian-cli"
    authoritative = True

    #: Command surface, in one place, so that correcting it is a one-line edit.
    COMMANDS = {
        "version": ["version"],
        "unresolved": ["unresolved", "format=json"],
        "orphans": ["orphans", "format=json"],
        "deadends": ["deadends", "format=json"],
        "backlinks": ["backlinks", "path={path}", "format=json"],
        "move": ["move", "path={src}", "to={dst}"],
    }

    #: Printed by an installer too old to have full CLI support. It goes to
    #: stdout, with a zero exit code and no JSON, so it has to be recognised by
    #: name to be reported as the failure it is.
    STALE_INSTALLER = "installer is out of date"

    def __init__(self, vault: Path, exe: str | None = None) -> None:
        self.vault = vault
        self.exe = exe or shutil.which("obsidian") or "obsidian"

    def answers_queries(self) -> tuple[bool, str]:
        """Does this CLI actually return data, not just a version string?

        ``obsidian version`` answering is not enough to trust the backend: the
        version command works on installers whose query commands do not. This
        asks a real question and insists on a real answer.
        """
        res = self.run("unresolved")
        if res.ok:
            return True, "answers queries"
        text = (res.err or res.out).strip().replace("\n", " ")
        if self.STALE_INSTALLER in text:
            return False, ("Obsidian installer is too old for CLI queries; "
                           "download the latest installer from obsidian.md/download")
        return False, text[:200] or "no answer"

    # -- plumbing ---------------------------------------------------------

    def run(self, command: str, **params: str) -> CliResult:
        args = [part.format(**params) for part in self.COMMANDS[command]]
        try:
            done = subprocess.run(
                [self.exe, f"vault={self.vault.name}", *args],
                cwd=self.vault,
                capture_output=True,
                text=True,
                timeout=TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return CliResult(False, "", f"{command}: timed out after {TIMEOUT:g}s", None)
        out, err = done.stdout or "", done.stderr or ""
        parsed: object | None = None
        try:
            parsed = json.loads(out) if out.strip() else None
        except json.JSONDecodeError:
            parsed = None
        # The exit code is not evidence. Output that parses, or output that is
        # plainly not an error, is.
        wants_json = any(part == "format=json" for part in args)
        if not out.strip() and not err.strip():
            # An empty result is a legitimate answer: no orphans, say.
            ok = True
        elif wants_json:
            # We asked for JSON, so JSON is the only evidence of success.
            #
            # This used to accept "stdout is non-empty and stderr is empty",
            # which an older Obsidian installer satisfies by printing
            #   Your Obsidian installer is out of date … better CLI support
            # to stdout and no JSON at all. Every query then looked like it
            # succeeded and returned nothing, which made the gate's
            # link-integrity rule a silent no-op: no broken links before, none
            # after, nothing ever fails. A check that cannot fail is worse than
            # one that is absent, because it is counted on.
            ok = parsed is not None
        else:
            ok = bool(out.strip()) and not err.strip()
        return CliResult(ok, out, err, parsed)

    def _list_of_notes(self, command: str) -> list[str]:
        res = self.run(command)
        if not res.ok:
            raise OpsUnavailable(f"obsidian {command}: {res.err or res.out}".strip())
        return sorted(_paths(res.parsed if res.parsed is not None else res.out))

    # -- interface --------------------------------------------------------

    def refresh(self) -> None:
        """Obsidian keeps its own metadata cache current; nothing to do."""

    def unresolved(self) -> list[BrokenLink]:
        res = self.run("unresolved")
        if not res.ok:
            raise OpsUnavailable(f"obsidian unresolved: {res.err or res.out}".strip())
        out: list[BrokenLink] = []
        if isinstance(res.parsed, list):
            for item in res.parsed:
                if isinstance(item, dict):
                    out.append(
                        BrokenLink(
                            note=str(item.get("path") or item.get("source") or item.get("file") or ""),
                            target=str(item.get("link") or item.get("target") or item.get("unresolved") or ""),
                            line=int(item.get("line") or 0),
                        )
                    )
                else:
                    out.append(BrokenLink(note="", target=str(item)))
        elif isinstance(res.parsed, dict):
            for note, targets in res.parsed.items():
                for target in targets or []:
                    out.append(BrokenLink(note=str(note), target=str(target)))
        return sorted(out, key=lambda b: (b.note, b.target))

    def orphans(self) -> list[str]:
        return self._list_of_notes("orphans")

    def deadends(self) -> list[str]:
        return self._list_of_notes("deadends")

    def backlinks(self, rel: str) -> list[str]:
        res = self.run("backlinks", path=rel)
        if not res.ok:
            raise OpsUnavailable(f"obsidian backlinks: {res.err or res.out}".strip())
        return sorted(_paths(res.parsed if res.parsed is not None else res.out))

    def move(self, src_rel: str, dst_rel: str) -> None:
        """Move through Obsidian, so Obsidian rewrites the inbound links.

        Verified against the filesystem afterwards, because the exit code is
        not evidence and this is the one operation that can quietly maim the
        graph.
        """
        res = self.run("move", src=src_rel, dst=dst_rel)
        if not (self.vault / dst_rel).exists():
            raise OpsUnavailable(
                f"obsidian move did not produce {dst_rel}: {res.err or res.out}".strip()
            )
        if (self.vault / src_rel).exists():
            raise OpsUnavailable(f"obsidian move left {src_rel} in place")


def _paths(payload: object) -> list[str]:
    if payload is None:
        return []
    if isinstance(payload, str):
        return [line.strip() for line in payload.splitlines() if line.strip()]
    if isinstance(payload, list):
        out = []
        for item in payload:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                value = item.get("path") or item.get("file") or item.get("name")
                if value:
                    out.append(str(value))
        return out
    if isinstance(payload, dict):
        return _paths(payload.get("results") or payload.get("files") or [])
    return []
