"""Obsidian CLI backend.

Verified against Obsidian installer 1.13.x on 2026-09-22: ``unresolved``,
``orphans`` and ``deadends`` all answer. ``move`` and ``backlinks`` have still
never been run. ``tiro doctor`` exercises the queries and reports which failed.

Three rules from the design apply to every call here (DESIGN section 3.4), plus
one the real CLI taught us:

* **Exit codes are always 0**, even on failure — so never branch on returncode
  alone; parse the output, and verify the effect afterwards.
* **About a second per command**, with no batching — so use the vault-wide
  queries, never a per-note loop.
* **Obsidian writes chatter to stdout**: a startup log line, and on an old
  installer a banner telling you to update. It is not the answer, and it used
  to be mistaken for one.
* **`format=json` is a request, not a contract.** ``unresolved`` honours it;
  ``orphans`` and ``deadends`` ignore it and print one path per line. Both
  shapes are accepted. And the JSON ``unresolved`` returns names the broken
  *target* but not the note it was found in, so ``BrokenLink.note`` is empty
  on this backend — enough for the gate, which compares sets of targets before
  and after, and less than the filesystem backend gives lint.
"""

from __future__ import annotations

import json
import re
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
        # Strip the chatter Obsidian writes to stdout — a startup log line, or
        # the out-of-date-installer banner — before judging the answer.
        #
        # Success used to mean "stdout non-empty, stderr empty", which the
        # banner satisfies while carrying no data. Every query then looked
        # fine and returned nothing, and the gate's link-integrity rule became
        # a silent no-op: no broken links before a job, none after, nothing
        # ever fails. A check that cannot fail is worse than one that is
        # absent, because it is counted on.
        #
        # Insisting on JSON instead was too strict the other way: `orphans`
        # and `deadends` ignore `format=json` and answer with one path per
        # line, which is a perfectly good answer. So the test is "is there
        # anything left once the chatter is gone", and JSON is preferred when
        # it parses.
        data = _without_chatter(out)
        if not data and not err.strip():
            ok = True  # an empty result is a legitimate answer: no orphans
        else:
            ok = parsed is not None or (bool(data) and not err.strip())
        return CliResult(ok, data or out, err, parsed)

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


#: Lines Obsidian prints to stdout that are not the answer: its startup log
#: (leading ISO timestamp) and the banner an old installer shows.
_CHATTER = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}\b.*|Your Obsidian installer .*|.*"
    r"download the latest installer.*)$"
)


def _without_chatter(out: str) -> str:
    """``out`` with Obsidian's log and banner lines removed.

    Returns "" when nothing but chatter was printed, which is how a command
    that answered nothing is told from one that answered a banner.
    """
    kept = [line for line in out.splitlines() if line.strip() and not _CHATTER.match(line.strip())]
    return "\n".join(kept).strip()


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
