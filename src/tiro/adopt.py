"""`tiro adopt`: read the vault, propose its `.tiro/` files (ITERATION-1, M1).

Adopt, don't impose. The vault already has a shape — even a vault with "no
structure" has one: where the daily notes go, which folders are live and which
are an old import, what sits loose at the root. This module measures that shape
and drafts `rules.md` and `trust.toml` from it, into `.tiro/proposals/adopt/`,
for the user to read, edit and move into place. Tiro reads nothing from the
proposals directory itself.

Everything here is heuristic and says so in the draft. The point is that the
user edits five lines rather than writing fifty.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from tiro import protocol
from tiro.config import ROOT, Config
from tiro.links import iter_links
from tiro.lint import UNTITLED
from tiro.scan import is_daily, is_hidden, iter_notes

#: Folder names that mean "an old import or a parking lot", in the languages
#: this vault's owner is likely to use.
ARCHIVE_NAMES = re.compile(r"^(roam|archiv|archive|archives|import|imports|export|old|alt|legacy)\b", re.I)
INBOX_NAMES = re.compile(r"^(\d+\s*)?(inbox|eingang|posteingang|capture|in)$", re.I)

#: An import folder is one whose newest note is this old, and which is mostly
#: daily pages: a diary nobody writes in any more.
ARCHIVE_IDLE_DAYS = 180

#: Lines a vault's .gitignore should have once Tiro runs in it (DESIGN §6).
GITIGNORE = [".obsidian/workspace.json", ".trash/", ".tiro/lock", ".tiro/runs/*/transcript.jsonl"]

#: A tag Obsidian would parse but that is really a CSS colour or a URL fragment.
NOISE_TAG = re.compile(r"^#([0-9a-f]{3}|[0-9a-f]{6})$", re.I)
_TAG = re.compile(r"(?<![\w/#])#([A-Za-z][\w/-]*)")


@dataclass
class Folder:
    name: str  # top-level folder, or "/" for the root itself
    notes: int = 0
    daily: int = 0
    newest: float | None = None  # mtime of the most recently touched note
    other_files: int = 0

    @property
    def loose(self) -> int:
        return self.notes - self.daily


@dataclass
class Survey:
    notes: int = 0
    folders: list[Folder] = field(default_factory=list)
    daily_home: str | None = None
    inbox: str | None = None
    archives: list[str] = field(default_factory=list)
    frontmatter_keys: Counter = field(default_factory=Counter)
    tags: Counter = field(default_factory=Counter)
    plugins: list[str] = field(default_factory=list)
    links: int = 0
    empty: int = 0
    empty_daily: int = 0
    untitled: int = 0
    requests: list[str] = field(default_factory=list)
    near_misses: list[str] = field(default_factory=list)
    has_rules: bool = False
    has_trust: bool = False
    gitignore_missing: list[str] = field(default_factory=list)

    def folder(self, name: str) -> Folder | None:
        return next((f for f in self.folders if f.name == name), None)


def survey(config: Config, *, now: float | None = None) -> Survey:
    now = time.time() if now is None else now
    vault = config.vault
    out = Survey()
    by_name: dict[str, Folder] = {}

    def bucket(rel: Path) -> Folder:
        name = rel.parts[0] if len(rel.parts) > 1 else ROOT
        return by_name.setdefault(name, Folder(name))

    for path in sorted(vault.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(vault)
        if is_hidden(rel.parts) or rel.parts[0] == "Tiro":
            continue
        folder = bucket(rel)
        if path.suffix != ".md":
            folder.other_files += 1
            if UNTITLED.match(path.stem):
                out.untitled += 1
            continue
        out.notes += 1
        folder.notes += 1
        stat = path.stat()
        folder.newest = stat.st_mtime if folder.newest is None else max(folder.newest, stat.st_mtime)
        daily = is_daily(rel)
        if daily:
            folder.daily += 1
        if stat.st_size == 0:
            if daily:
                out.empty_daily += 1
            else:
                out.empty += 1
        if UNTITLED.match(path.stem):
            out.untitled += 1

        text = path.read_text(encoding="utf-8", errors="replace")
        fm, body, present = protocol.split_frontmatter(text)
        if present:
            for line in fm:
                m = re.match(r"^([A-Za-z_][\w/-]*)\s*:", line)
                if m:
                    out.frontmatter_keys[m.group(1)] += 1
        for m in _TAG.finditer(protocol.without_code(body)):
            tag = "#" + m.group(1)
            if not NOISE_TAG.match(tag):
                out.tags[tag] += 1
        out.links += len(iter_links(text))
        rel_posix = rel.as_posix()
        if protocol.verb(text) is not None:
            out.requests.append(rel_posix)
        elif protocol.looks_like_request(text):
            out.near_misses.append(rel_posix)

    out.folders = sorted(by_name.values(), key=lambda f: (-f.notes, f.name))

    # An archive: named like one, or a diary nobody writes in any more.
    for f in out.folders:
        if f.name == ROOT:
            continue
        idle = (now - f.newest) / 86400 if f.newest is not None else 0
        named = bool(ARCHIVE_NAMES.match(f.name))
        dormant = f.notes >= 20 and f.daily / f.notes >= 0.8 and idle > ARCHIVE_IDLE_DAYS
        if named or dormant:
            out.archives.append(f.name)

    live = [f for f in out.folders if f.name not in out.archives]
    with_daily = [f for f in live if f.daily]
    if with_daily:
        out.daily_home = max(with_daily, key=lambda f: f.daily).name

    named_inbox = next((f.name for f in live if f.name != ROOT and INBOX_NAMES.match(f.name)), None)
    root = out.folder(ROOT)
    if named_inbox:
        out.inbox = named_inbox
    elif root and root.loose >= 3:
        out.inbox = ROOT

    plugins = vault / ".obsidian" / "community-plugins.json"
    if plugins.exists():
        try:
            out.plugins = [str(p) for p in json.loads(plugins.read_text(encoding="utf-8"))]
        except (json.JSONDecodeError, TypeError):
            out.plugins = []

    out.has_rules = (config.tiro_dir / "rules.md").exists()
    out.has_trust = (config.tiro_dir / "trust.toml").exists()
    gitignore = vault / ".gitignore"
    present = set(gitignore.read_text(encoding="utf-8").split("\n")) if gitignore.exists() else set()
    out.gitignore_missing = [line for line in GITIGNORE if line not in present]
    return out


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------


def _label(name: str) -> str:
    return "the vault root" if name == ROOT else f"`{name}/`"


def render_survey(s: Survey) -> str:
    lines = [f"{s.notes} notes, {s.links} links between them."]
    lines.append("")
    lines.append(f"{'folder':<28}{'notes':>6}{'daily':>6}{'loose':>6}{'other':>6}")
    for f in s.folders[:15]:
        tag = "  archive" if f.name in s.archives else ""
        lines.append(f"{f.name:<28}{f.notes:>6}{f.daily:>6}{f.loose:>6}{f.other_files:>6}{tag}")
    if len(s.folders) > 15:
        lines.append(f"…and {len(s.folders) - 15} more folders")
    lines.append("")
    lines.append(f"daily notes live in {_label(s.daily_home) if s.daily_home else 'no one place'}")
    lines.append(f"inbox: {_label(s.inbox) if s.inbox else 'none found'}")
    lines.append(f"archives: {', '.join(s.archives) or 'none'}")
    keys = ", ".join(f"{k} ({n})" for k, n in s.frontmatter_keys.most_common(6)) or "none"
    lines.append(f"frontmatter keys in use: {keys}")
    tags = ", ".join(f"{t} ({n})" for t, n in s.tags.most_common(8) if n >= 2) or "none used twice"
    lines.append(f"tags used more than once: {tags}")
    lines.append(f"plugins: {', '.join(s.plugins) or 'none'}")
    lines.append(f"leftovers: {s.empty} empty notes, {s.empty_daily} empty daily notes, "
                 f"{s.untitled} never named")
    if s.requests:
        lines.append(f"requests waiting: {', '.join(s.requests)}")
    if s.near_misses:
        lines.append(f"looks like a request but is not one Tiro reads: {', '.join(s.near_misses)}")
    lines.append(f".tiro/rules.md: {'present' if s.has_rules else 'absent'}; "
                 f".tiro/trust.toml: {'present' if s.has_trust else 'absent'}")
    if s.gitignore_missing:
        lines.append(f".gitignore is missing: {', '.join(s.gitignore_missing)}")
    return "\n".join(lines)


def draft_trust(s: Survey) -> str:
    lines = [
        "# Drafted by `tiro adopt`. Edit, then move to .tiro/trust.toml.",
        "#",
        "# L0 read only · L1 Tiro's own blocks · L2 + tiro/* keys · L3 + create notes",
        "# · L4 + move. A note you tag is L2 for itself whatever its folder says,",
        "# unless the folder is L0. Filing needs L3 on the destination.",
        "#",
        "# The default below is permissive on purpose: every folder is a place",
        "# Tiro may file into. Put a folder at L0 or L1 to keep it out.",
        "",
        'default = "L3"',
        '"Tiro/" = "L4"',
    ]
    if s.inbox:
        lines.append(f'"{s.inbox}" = "L4"    # the inbox: things are meant to leave it')
    for name in s.archives:
        lines.append(f'"{name}/" = "L1"    # an import; read, never filed into')
    lines.append("")
    lines.append("# Private? Add a line like:")
    lines.append('# "Journal/" = "L0"')
    return "\n".join(lines) + "\n"


def draft_rules(s: Survey, *, today: str) -> str:
    n = 0

    def rule(title: str, body: str, *, since: str = today) -> str:
        nonlocal n
        n += 1
        return f"### R-{n:03d} — {title}\nSince {since}. Drafted by `tiro adopt`; edit freely.\n{body}\n"

    out = [
        "# Filing rules for this vault",
        "",
        "Drafted by `tiro adopt` from what the vault already does. Nothing here is",
        "binding until you move this file to `.tiro/rules.md`. Delete what is",
        "wrong, sharpen what is vague. Tiro proposes changes to this file; it",
        "never edits it.",
        "",
    ]
    if s.daily_home:
        out.append(rule(
            f"Daily notes stay in {_label(s.daily_home)}",
            f"A note named `YYYY-MM-DD` is a daily note. {s.folder(s.daily_home).daily} live "
            f"there. They are never filed anywhere else, never proposed for a folder, and "
            f"an empty one is not a leftover.",
        ))
    live = [f for f in s.folders if f.name != ROOT and f.name not in s.archives and f.loose]
    if live:
        listing = ", ".join(f"`{f.name}/` ({f.loose})" for f in live[:12])
        out.append(rule(
            "Folders are contexts",
            f"Each top-level folder is a context — an employer, a project, a topic — not a "
            f"type of note: {listing}. A note goes with the context it belongs to. A "
            f"new context gets a new top-level folder; say so in the proposal.",
        ))
    if s.inbox:
        out.append(rule(
            f"Loose notes in {_label(s.inbox)} are the inbox",
            "A note here that is not a daily note has not been filed yet. Triage proposes "
            "a context folder for it, by analogy with where similar notes already live. "
            "If nothing similar exists, propose a new folder rather than asking.",
        ))
    for name in s.archives:
        f = s.folder(name)
        out.append(rule(
            f"`{name}/` is an archive",
            f"{f.notes} notes, mostly older daily pages. Read it, link to it, quote it. "
            f"Never file into it and never propose moving anything out of it.",
        ))
    out.append(rule(
        "Leftovers go to `Archive/`, on request",
        "Empty notes, never-named files and fragments too short to be a note are "
        "listed by `lint`. Tag one `tiro: triage` and Tiro proposes `Archive/` as its "
        "destination. Nothing is ever deleted.",
    ))
    out.append(rule(
        "Match the note's language",
        "Notes here are in German and English, often both. Tiro writes in the language "
        "the note is in.",
    ))
    return "\n".join(out)


def write_proposals(config: Config, s: Survey) -> list[Path]:
    out = config.tiro_dir / "proposals" / "adopt"
    out.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    written = []
    for name, text in [
        ("survey.md", f"# Vault survey, {today}\n\n```\n{render_survey(s)}\n```\n"),
        ("rules.md", draft_rules(s, today=today)),
        ("trust.toml", draft_trust(s)),
    ]:
        path = out / name
        path.write_text(text, encoding="utf-8")
        written.append(path)
    if s.gitignore_missing:
        path = out / "gitignore"
        path.write_text("# append to the vault's .gitignore\n" + "\n".join(s.gitignore_missing) + "\n",
                        encoding="utf-8")
        written.append(path)
    if s.inbox:
        path = out / "tiro.toml"
        path.write_text(f'# add to tiro.toml in the tiro repo\n[lint]\ninbox = "{s.inbox}"\n',
                        encoding="utf-8")
        written.append(path)
    return written
