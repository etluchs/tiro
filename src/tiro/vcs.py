"""Git is Tiro's transaction log, audit trail and undo button (DESIGN section 6).

One job, one commit, with trailers. That is what makes a bad job one ``git
revert`` away and stops it taking a good one with it.
"""

from __future__ import annotations

import os
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

STALE_LOCK_SECONDS = 30 * 60


class GitError(Exception):
    pass


class LockBusy(Exception):
    """Another run holds the vault. Not an error — just not our turn."""


@dataclass
class Git:
    repo: Path

    def __call__(self, *args: str, check: bool = True) -> str:
        done = subprocess.run(
            ["git", *args], cwd=self.repo, capture_output=True, text=True
        )
        if check and done.returncode != 0:
            raise GitError(f"git {' '.join(args)}: {done.stderr.strip() or done.stdout.strip()}")
        return done.stdout

    # -- state ------------------------------------------------------------

    def is_repo(self) -> bool:
        try:
            self("rev-parse", "--git-dir")
            return True
        except GitError:
            return False

    def head(self) -> str:
        return self("rev-parse", "HEAD").strip()

    def dirty_paths(self) -> list[str]:
        """Vault-relative paths with any uncommitted change, staged or not."""
        out = []
        for line in self("status", "--porcelain", "-z").split("\0"):
            if len(line) > 3:
                out.append(line[3:])
        return sorted(out)

    def has_remote(self) -> bool:
        return bool(self("remote", check=False).strip())

    def can_push(self) -> tuple[bool, str]:
        if not self.has_remote():
            return False, "no remote configured"
        done = subprocess.run(
            ["git", "push", "--dry-run", "--porcelain"],
            cwd=self.repo, capture_output=True, text=True, timeout=60,
        )
        if done.returncode != 0:
            return False, (done.stderr.strip() or "push refused").splitlines()[-1][:200]
        return True, "ok"

    # -- operations -------------------------------------------------------

    def pull_rebase(self) -> tuple[bool, str]:
        """Rebase onto the remote. On conflict, abort — never merge user prose.

        Returns (ok, detail). A failure here ends the run before any job starts,
        which is the point: better no work than work on top of a conflict.
        """
        if not self.has_remote():
            return True, "no remote; nothing to pull"
        done = subprocess.run(
            ["git", "pull", "--rebase", "--autostash"],
            cwd=self.repo, capture_output=True, text=True, timeout=300,
        )
        if done.returncode == 0:
            return True, done.stdout.strip().splitlines()[-1][:200] if done.stdout.strip() else "up to date"
        subprocess.run(["git", "rebase", "--abort"], cwd=self.repo, capture_output=True)
        subprocess.run(["git", "stash", "pop"], cwd=self.repo, capture_output=True)
        return False, (done.stderr.strip() or done.stdout.strip()).splitlines()[-1][:300]

    def commit(self, paths: list[str], message: str, trailers: dict[str, str]) -> str | None:
        """Commit exactly these paths. Returns the sha, or None if nothing changed."""
        # A path that neither exists nor is tracked has nothing to stage — the
        # source of a move whose note was never committed. Naming it anyway makes
        # `git add` refuse the whole commit.
        paths = [p for p in paths
                 if (self.repo / p).exists()
                 or self("ls-files", "--error-unmatch", "--", p, check=False).strip()]
        if not paths:
            return None
        self("add", "--", *paths)
        staged = self("diff", "--cached", "--name-only").strip()
        if not staged:
            return None
        body = message.rstrip() + "\n\n" + "\n".join(f"{k}: {v}" for k, v in trailers.items())
        self("commit", "-q", "-m", body)
        return self.head()

    def push(self) -> tuple[bool, str]:
        done = subprocess.run(
            ["git", "push"], cwd=self.repo, capture_output=True, text=True, timeout=300
        )
        if done.returncode != 0:
            return False, (done.stderr.strip() or "push failed").splitlines()[-1][:300]
        return True, "pushed"

    def commits_for_run(self, run_id: str) -> list[str]:
        out = self("log", f"--grep=^Tiro-Run: {run_id}$", "--format=%H", "--extended-regexp")
        return [line for line in out.split() if line]

    def revert(self, shas: list[str]) -> None:
        """Revert newest first, so each revert applies cleanly."""
        for sha in shas:
            self("revert", "--no-edit", sha)


@contextmanager
def run_lock(path: Path):
    """One run at a time. The Obsidian CLI and git both act under this lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            age = time.time() - path.stat().st_mtime
            holder = path.read_text(encoding="utf-8").strip()
        except OSError:
            age, holder = 0.0, "unreadable"
        if age < STALE_LOCK_SECONDS:
            raise LockBusy(f"vault locked by {holder} ({age:.0f}s ago)")
        path.unlink(missing_ok=True)
    path.write_text(f"pid={os.getpid()} started={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n",
                    encoding="utf-8")
    try:
        yield
    finally:
        path.unlink(missing_ok=True)
