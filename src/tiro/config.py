"""Configuration: tiro.toml (repo side) and .tiro/trust.toml (vault side).

The split is by mutability, not by location for its own sake: the repo holds
what a human edits and reviews with the code, the vault holds what changes as
the vault changes. See DESIGN section 3.1.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

TRUST_LEVELS = ("L0", "L1", "L2", "L3", "L4", "L5")

#: What each level permits. L5 is defined so that it can be refused by name.
TRUST_MEANING = {
    "L0": "read and report only",
    "L1": "append inside Tiro's own blocks",
    "L2": "L1 + write tiro/* frontmatter keys",
    "L3": "L2 + create new notes",
    "L4": "L3 + move and rename, rewriting inbound links",
    "L5": "delete — never implemented",
}


def as_vault_path(value: str | Path) -> str:
    """Normalise to the vault-relative POSIX form every comparison assumes."""
    return str(value).replace("\\", "/").lstrip("/")


#: The one folder key that is not a prefix: ``"/"`` names the vault root itself,
#: meaning files that sit directly in it and nothing below. A vault that keeps
#: its daily notes and loose captures at the top level has no inbox folder to
#: point at; this is how it points at the root instead.
ROOT = "/"


def in_folder(rel_path: str | Path, folder: str) -> bool:
    """Is this path inside that folder? ``"/"`` matches root-level files only.

    Matching is by path component, not by string prefix: ``Areas`` does not
    claim ``Areas2/...``. A trailing slash on the folder is optional.
    """
    rel = as_vault_path(rel_path)
    if folder == ROOT:
        return "/" not in rel
    prefix = as_vault_path(folder).rstrip("/")
    if not prefix:
        return True
    return rel == prefix or rel.startswith(prefix + "/")


class ConfigError(Exception):
    """Raised when configuration is missing or nonsensical. Always fatal."""


def _level_index(level: str) -> int:
    try:
        return TRUST_LEVELS.index(level)
    except ValueError:
        raise ConfigError(f"unknown trust level {level!r}") from None


@dataclass(frozen=True)
class TrustMap:
    """Per-folder trust ladder (DESIGN section 5.1).

    Lookup is longest-prefix: the most specific configured folder wins. Paths
    are vault-relative, POSIX-style, and folders end in "/". The key ``"/"``
    is the vault root itself: files directly in it, not everything below.
    """

    default: str = "L1"
    folders: dict[str, str] = field(default_factory=dict)

    def level_for(self, rel_path: str | Path) -> str:
        rel = as_vault_path(rel_path)
        best, best_len = self.default, -1
        for folder, level in self.folders.items():
            if not in_folder(rel, folder):
                continue
            depth = 0 if folder == ROOT else len(as_vault_path(folder).rstrip("/"))
            if depth > best_len:
                best, best_len = level, depth
        return best

    def permits(self, rel_path: str | Path, required: str) -> bool:
        return _level_index(self.level_for(rel_path)) >= _level_index(required)

    @staticmethod
    def index(level: str) -> int:
        return _level_index(level)

    @classmethod
    def load(cls, path: Path) -> "TrustMap":
        if not path.exists():
            # Absent trust.toml means the lowest useful level everywhere. Tiro
            # is readable and appends to its own blocks; nothing else.
            return cls()
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        default = raw.pop("default", "L1")
        folders: dict[str, str] = {}
        for key, value in raw.items():
            if not isinstance(value, str):
                raise ConfigError(f"trust.toml: {key!r} must be a level string")
            if key != ROOT and not as_vault_path(key).strip("/ "):
                raise ConfigError('trust.toml: an empty folder key is ambiguous; use "/" for the root')
            _level_index(value)
            folders[key] = value
        _level_index(default)
        if "L5" in {default, *folders.values()}:
            raise ConfigError("trust.toml: L5 (delete) is not implemented and never will be")
        return cls(default=default, folders=folders)


@dataclass(frozen=True)
class RunConfig:
    """Bounds on one run, and on a day of runs (DESIGN section 5.4).

    The two per-day ceilings are what makes a timer safe to leave on: the
    per-run caps bound one pass, but nothing bounded forty-eight passes until
    these existed. ``None`` means no ceiling, which is the default only because
    the right number needs a week of real traffic to pick (DESIGN open
    question 3). On a Claude subscription the SDK often reports no dollar
    figure, so ``max_tokens_per_day`` is the lever that always works.
    """

    max_jobs: int = 20
    max_seconds: int = 900
    skip_recent_seconds: int = 60
    max_attempts_per_note_per_day: int = 3
    max_cost_usd_per_day: float | None = None
    max_tokens_per_day: int | None = None
    push: bool = True


@dataclass(frozen=True)
class AgentConfig:
    model: str = "claude-opus-5"
    max_turns: int = 30


@dataclass(frozen=True)
class DispatchConfig:
    site: str = ""
    project: str = ""
    issue_type: str = "Task"
    live: bool = False


@dataclass(frozen=True)
class LintConfig:
    """Where lint looks for things that should have moved on.

    ``inbox`` is a folder, or ``"/"`` for loose notes at the vault root. Empty
    means the vault has no inbox, and the stale check is skipped rather than
    pointed at a folder that does not exist.
    """

    inbox: str = ""
    stale_days: int = 30
    tiny_bytes: int = 50


@dataclass(frozen=True)
class Config:
    root: Path
    """The tiro repo."""
    vault: Path
    run: RunConfig = field(default_factory=RunConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    dispatch: DispatchConfig = field(default_factory=DispatchConfig)
    lint: LintConfig = field(default_factory=LintConfig)
    jobs: dict[str, dict] = field(default_factory=dict)
    trust: TrustMap = field(default_factory=TrustMap)

    @property
    def tiro_dir(self) -> Path:
        """Tiro's state inside the vault."""
        return self.vault / ".tiro"

    @property
    def notes_dir(self) -> Path:
        """Tiro's own user-facing notes."""
        return self.vault / "Tiro"

    @classmethod
    def load(cls, root: Path | None = None, vault: Path | None = None) -> "Config":
        root = Path(root or Path.cwd()).resolve()
        raw: dict = {}
        cfg_path = root / "tiro.toml"
        if cfg_path.exists():
            raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
        elif vault is None:
            raise ConfigError(
                f"no tiro.toml in {root} and no vault given; copy tiro.toml.example"
            )

        if vault is None:
            declared = raw.get("vault")
            if not declared:
                raise ConfigError("tiro.toml has no 'vault' key")
            vault_path = (root / declared).resolve()
        else:
            vault_path = Path(vault).resolve()

        if not vault_path.is_dir():
            raise ConfigError(f"vault {vault_path} is not a directory")

        trust = TrustMap.load(vault_path / ".tiro" / "trust.toml")
        return cls(
            root=root,
            vault=vault_path,
            run=RunConfig(**raw.get("run", {})),
            agent=AgentConfig(**raw.get("agent", {})),
            dispatch=DispatchConfig(**raw.get("dispatch", {})),
            lint=LintConfig(**raw.get("lint", {})),
            jobs=raw.get("jobs", {}),
            trust=trust,
        )
