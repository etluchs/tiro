"""Vault operations: one interface, two backends (DESIGN section 3.4).

Where the Obsidian CLI answers, we use it — it owns link resolution and it
rewrites links on a move, which is the whole reason ``file`` is safe. Where it
does not, the filesystem backend takes over with its own approximation, and says
so in the report rather than claiming authority it does not have.

Writes are *not* in this interface. Tiro serialises its own frontmatter and
blocks onto the filesystem, because the gate has to reason about a git diff
rather than about what a subprocess claims it did.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from tiro.failure import MachineFailure


class OpsUnavailable(Exception):
    """This backend cannot do that. Never a reason to do it some other way."""


class OpsDown(OpsUnavailable, MachineFailure):
    """The Obsidian CLI is not there to ask — the app is closed, or it did not
    answer. The same job will work once it is running again."""


@dataclass(frozen=True)
class BrokenLink:
    note: str
    target: str
    line: int = 0


class VaultOps(Protocol):
    name: str
    authoritative: bool

    def refresh(self) -> None: ...
    def unresolved(self) -> list[BrokenLink]: ...
    def orphans(self) -> list[str]: ...
    def deadends(self) -> list[str]: ...
    def backlinks(self, rel: str) -> list[str]: ...
    def move(self, src_rel: str, dst_rel: str) -> None: ...


@dataclass(frozen=True)
class Probe:
    installed: bool
    responding: bool
    detail: str

    @property
    def usable(self) -> bool:
        return self.installed and self.responding


def probe_obsidian(timeout: float = 5.0) -> Probe:
    """Is the Obsidian CLI there and answering?

    ``obsidian version`` is documented to *launch* Obsidian if it is not
    running, which is not something a background timer should do behind the
    user's back. So the probe is cheap and bounded, a timeout counts as absent,
    and ``[ops] backend = "fs"`` opts out of running it at all.
    """
    exe = shutil.which("obsidian")
    if not exe:
        return Probe(False, False, "obsidian not on PATH")
    try:
        done = subprocess.run(
            [exe, "version"], capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return Probe(True, False, f"no answer within {timeout:g}s (app not running?)")
    except OSError as exc:  # pragma: no cover - platform specific
        return Probe(True, False, f"could not run: {exc}")
    if done.returncode != 0:
        return Probe(True, False, (done.stderr or done.stdout).strip()[:200])
    return Probe(True, True, (done.stdout or "").strip()[:200])


#: What the desktop app's process is called. macOS capitalises it; Linux does
#: not. Both are tried because getting it wrong fails safe in the wrong
#: direction: a running Obsidian we fail to see only costs us the CLI backend.
_PROCESS_NAMES = ("Obsidian", "obsidian")


def obsidian_is_running(timeout: float = 2.0) -> bool:
    """Is the desktop app already up?

    ``probe_obsidian`` *launches* Obsidian when it is not running. That is
    acceptable when a human typed the command and wrong when a timer fired it —
    opening an app on someone's laptop at 03:00 is not a thing a background job
    may decide to do. So ``auto`` asks this first and settles for the filesystem
    backend rather than starting anything.

    Anything we cannot answer counts as "not running", because the cost of
    being wrong that way is a weaker backend, and the cost of being wrong the
    other way is an app nobody asked for.
    """
    exe = shutil.which("pgrep")
    if not exe:  # pragma: no cover - platform specific
        return False
    for name in _PROCESS_NAMES:
        try:
            done = subprocess.run(
                [exe, "-x", name], capture_output=True, text=True, timeout=timeout
            )
        except (subprocess.TimeoutExpired, OSError):  # pragma: no cover
            return False
        if done.returncode == 0 and done.stdout.strip():
            return True
    return False


def for_vault(vault: Path, *, backend: str = "auto", timeout: float = 5.0) -> VaultOps:
    """Choose a backend.

    ``fs`` never probes. ``cli`` always probes and may therefore launch
    Obsidian — that is what asking for it explicitly means. ``auto`` probes only
    when the app is already up, so the backend follows reality: full adapter at
    your desk, filesystem fallback on a timer at night.
    """
    from tiro.ops_cli import ObsidianCliOps
    from tiro.ops_fs import FilesystemOps

    if backend == "fs":
        return FilesystemOps(vault)
    if backend == "cli":
        probe = probe_obsidian(timeout)
        if not probe.usable:
            raise OpsDown(f"obsidian CLI required but unusable: {probe.detail}")
        cli = ObsidianCliOps(vault)
        answers, why = cli.answers_queries()
        if not answers:
            raise OpsDown(f"obsidian CLI required but not answering: {why}")
        return cli
    if not obsidian_is_running():
        return FilesystemOps(vault)
    if not probe_obsidian(timeout).usable:
        return FilesystemOps(vault)
    # Version answering is not enough. An installer can be new enough to report
    # a version and too old to answer a query, and a backend that returns
    # nothing to every question disables the gate's link check without a word.
    cli = ObsidianCliOps(vault)
    return cli if cli.answers_queries()[0] else FilesystemOps(vault)
