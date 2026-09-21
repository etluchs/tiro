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
    are vault-relative, POSIX-style, and folders end in "/".
    """

    default: str = "L1"
    folders: dict[str, str] = field(default_factory=dict)

    def level_for(self, rel_path: str | Path) -> str:
        rel = str(rel_path).replace("\\\\", "/").lstrip("/")
        best, best_len = self.default, -1
        for prefix, level in self.folders.items():
            p = prefix.replace("\\\\", "/").lstrip("/")
            if rel.startswith(p) and len(p) > best_len:
                best, best_len = level, len(p)
        return best

    def permits(self, rel_path: str | Path, required: str) -> bool:
        return _level_index(self.level_for(rel_path)) >= _level_index(required)

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
            _level_index(value)
            folders[key] = value
        _level_index(default)
        if "L5" in {default, *folders.values()}:
            raise ConfigError("trust.toml: L5 (delete) is not implemented and never will be")
        return cls(default=default, folders=folders)


@dataclass(frozen=True)
class RunConfig:
    max_jobs: int = 20
    max_seconds: int = 900
    skip_recent_seconds: int = 60
    max_attempts_per_note_per_day: int = 3
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
class Config:
    root: Path
    """The tiro repo."""
    vault: Path
    run: RunConfig = field(default_factory=RunConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    dispatch: DispatchConfig = field(default_factory=DispatchConfig)
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
            jobs=raw.get("jobs", {}),
            trust=trust,
        )
