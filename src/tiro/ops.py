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


class OpsUnavailable(Exception):
    """This backend cannot do that. Never a reason to do it some other way."""


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


def for_vault(vault: Path, *, backend: str = "auto", timeout: float = 5.0) -> VaultOps:
    from tiro.ops_cli import ObsidianCliOps
    from tiro.ops_fs import FilesystemOps

    if backend == "fs":
        return FilesystemOps(vault)
    probe = probe_obsidian(timeout)
    if backend == "cli":
        if not probe.usable:
            raise OpsUnavailable(f"obsidian CLI required but unusable: {probe.detail}")
        return ObsidianCliOps(vault)
    return ObsidianCliOps(vault) if probe.usable else FilesystemOps(vault)
