"""Vault health, read-only (DESIGN section 8, `lint`).

The first job Tiro should be trusted with, because it proves it can read the
vault correctly before it is allowed to write to one. It touches nothing but
`Tiro/Health.md`.

The report always says which backend produced it. With the Obsidian CLI the
link data is Obsidian's own and therefore authoritative; without it, the numbers
come from our approximation of Obsidian's resolution rules, and a report that
claimed otherwise would be worse than no report.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import re

from tiro import index as folder_index
from tiro import protocol
from tiro.config import Config, in_folder
from tiro.journal import HEADER
from tiro.links import Index, iter_links
from tiro.ops import BrokenLink, VaultOps
from tiro.scan import is_daily, is_hidden, iter_notes

WORST = 20

#: Obsidian's placeholder names, in the languages it ships. A file still called
#: this is one the user opened and never came back to.
UNTITLED = re.compile(r"^(Untitled|Unbenannt|Ohne Titel|Sans titre|Senza titolo)( \d+)?$")

#: Tiro's own notes are not part of the vault's health. Counting them means the
#: report measures Tiro's presence: Health.md links to every broken note it
#: names, so those links count as broken too, and the numbers grow every run.
#: A trend column that moves because the report exists is worse than no trend.
OURS = "Tiro/"


@dataclass
class Finding:
    kind: str
    note: str
    detail: str = ""


@dataclass
class Report:
    backend: str
    authoritative: bool
    when: str
    notes: int = 0
    broken_links: list[Finding] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)
    protocol_problems: list[Finding] = field(default_factory=list)
    duplicate_titles: list[Finding] = field(default_factory=list)
    stale_inbox: list[Finding] = field(default_factory=list)
    stuck: list[Finding] = field(default_factory=list)
    leftovers: list[Finding] = field(default_factory=list)
    empty_daily: int = 0
    inbox: str = ""

    @property
    def counts(self) -> dict[str, int]:
        return {
            "notes": self.notes,
            "broken links": len(self.broken_links),
            "orphans": len(self.orphans),
            "protocol problems": len(self.protocol_problems),
            "duplicate titles": len(self.duplicate_titles),
            "stale in the inbox": len(self.stale_inbox),
            "stuck": len(self.stuck),
            "leftovers": len(self.leftovers) + (1 if self.empty_daily else 0),
        }


def run(config: Config, ops: VaultOps, *, now: float | None = None) -> Report:
    import time

    now = time.time() if now is None else now
    inbox, stale_days, tiny = config.lint.inbox, config.lint.stale_days, config.lint.tiny_bytes
    report = Report(
        backend=ops.name,
        authoritative=getattr(ops, "authoritative", False),
        when=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        inbox=inbox,
    )

    ops.refresh()
    for link in ops.unresolved():
        if link.note.startswith(OURS):
            continue
        report.broken_links.append(
            Finding("broken link", link.note, f"[[{link.target}]]"
                    + (f" (line {link.line})" if link.line else ""))
        )
    report.orphans = _orphans(config.vault)

    titles: dict[str, list[str]] = {}
    for path in iter_notes(config.vault):
        rel = path.relative_to(config.vault).as_posix()
        if rel.startswith(OURS):
            continue
        report.notes += 1
        daily = is_daily(rel)
        if not daily:
            titles.setdefault(path.stem.lower(), []).append(rel)

        text = path.read_text(encoding="utf-8", errors="replace")
        report.protocol_problems += _protocol_problems(rel, text)

        keys = protocol.read_keys(text)
        status = keys.get("tiro/status")
        if status in ("working", "blocked"):
            report.stuck.append(Finding(f"stuck: {status}", rel, keys.get("tiro/run", "")))

        size = path.stat().st_size
        if size == 0 and daily:
            # The daily-notes plugin creates one on open. Twenty-seven of them
            # are one fact, not twenty-seven findings.
            report.empty_daily += 1
        elif size == 0:
            report.leftovers.append(Finding("empty", rel))
        elif not daily and size < tiny and not text.strip().startswith("```"):
            report.leftovers.append(Finding("almost empty", rel, f"{size} bytes"))
        if not daily and UNTITLED.match(path.stem):
            report.leftovers.append(Finding("never named", rel))

        # Daily notes sit where the plugin put them, whatever folder that is;
        # they do not go stale and they are not filed.
        if inbox and not daily and in_folder(rel, inbox):
            age_days = (now - path.stat().st_mtime) / 86400
            if age_days > stale_days:
                report.stale_inbox.append(
                    Finding("stale", rel, f"{age_days:.0f} days untouched"))

    for stem, paths in sorted(titles.items()):
        if len(paths) > 1:
            report.duplicate_titles.append(
                Finding("duplicate title", paths[0], ", ".join(sorted(paths)[1:])))

    report.leftovers += _untitled_files(config.vault)
    report.leftovers = sorted(report.leftovers, key=lambda f: (f.note, f.kind))
    return report


def _untitled_files(vault: Path) -> list[Finding]:
    """Canvases, bases and the like that were opened once and never named.

    Not notes, so lint's other checks never see them; but they are the kind of
    thing that accumulates at the root of a vault and that the user is glad to
    have listed. Listed, only: Tiro never deletes.
    """
    out: list[Finding] = []
    for path in sorted(vault.rglob("*")):
        if not path.is_file() or path.suffix == ".md":
            continue
        rel_parts = path.relative_to(vault).parts
        if is_hidden(rel_parts) or rel_parts[0] == OURS.rstrip("/"):
            continue
        if UNTITLED.match(path.stem):
            out.append(Finding("never named", path.relative_to(vault).as_posix(),
                               f"{path.stat().st_size} bytes"))
    return out


def _orphans(vault: Path) -> list[str]:
    """Notes nothing links to — ignoring links from Tiro's own notes, and
    ignoring daily notes as candidates.

    Computed here rather than asked of the backend, and the reason is worth
    stating: the journal and this very report link to every note Tiro touches,
    so after one run almost nothing would be an orphan any more. Obsidian's own
    orphan list would say the same thing — correctly, and uselessly. Excluding
    ``Tiro/`` as a *source* of links keeps the number about the user's vault
    rather than about Tiro's bookkeeping.

    Daily notes are excluded as *targets* for a different reason: nothing links
    to a daily note by design, so in a vault that keeps a diary they are the
    whole list, and the notes that are actually adrift never surface.

    Folder indexes are Tiro's bookkeeping kept in the user's folders, and are
    excluded both ways for the same reason as ``Tiro/``: an index links to every
    note in its folder, so counting it would make every orphan disappear the
    day the index is made — and nothing links to an index but the user.
    """
    index = Index(vault)
    linked: set[str] = set()
    indexes: set[str] = set()
    for rel in sorted(index.paths):
        if not rel.endswith(".md") or rel.startswith(OURS):
            continue
        text = (vault / rel).read_text(encoding="utf-8", errors="replace")
        if folder_index.is_index(text):
            indexes.add(rel)
            continue
        for link in iter_links(text):
            target = index.resolve(link.target, from_rel=rel)
            if target and target != rel:
                linked.add(target)
    return sorted(
        rel for rel in index.paths
        if rel.endswith(".md") and rel not in linked and rel not in indexes
        and not rel.startswith(OURS) and not is_daily(rel)
    )


def _protocol_problems(rel: str, text: str) -> list[Finding]:
    out: list[Finding] = []
    keys = protocol.read_keys(text)

    verb = keys.get("tiro")
    if verb and verb not in protocol.VERBS:
        out.append(Finding("unknown verb", rel, f"`{verb}`"))

    status = keys.get("tiro/status")
    if status and status not in protocol.STATUSES:
        out.append(Finding("invalid status", rel, f"`{status}`"))

    blocks = protocol.find_blocks(text)
    if "<!-- tiro:begin" in text and not blocks:
        out.append(Finding("unclosed block", rel, "a begin marker with no end"))

    seen: set[str] = set()
    for block in blocks:
        if block.id in seen:
            out.append(Finding("duplicate block", rel, f"id={block.id}"))
        seen.add(block.id)

    if keys.get("tiro/hash") and not verb:
        out.append(Finding("orphaned state", rel, "a tiro/hash with no request"))

    if protocol.looks_like_request(text):
        out.append(Finding("unrecognised request", rel,
                           "a `#tiro` tag with no verb Tiro knows; try `#tiro research`"))

    return out


def write(config: Config, report: Report) -> Path:
    path = config.notes_dir / "Health.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = _previous_counts(config)

    lines = [f"---\ntitle: Tiro — vault health\n---\n", HEADER, ""]
    lines.append(f"Checked {report.when} using the `{report.backend}` backend.")
    if not report.authoritative:
        lines.append(
            "\n> [!warning] These link numbers are an approximation.\n"
            "> Obsidian was not reachable, so Tiro resolved links itself rather\n"
            "> than asking Obsidian's own index. Treat them as indicative."
        )
    lines.append("")
    lines.append("| | now | last time |")
    lines.append("|---|---:|---:|")
    for name, value in report.counts.items():
        was = previous.get(name)
        lines.append(f"| {name} | {value} | {'—' if was is None else was} |")
    lines.append("")

    for title, findings in [
        ("Broken links", report.broken_links),
        ("Protocol problems", report.protocol_problems),
        ("Stuck", report.stuck),
        ("Stale in the inbox", report.stale_inbox),
        ("Duplicate titles", report.duplicate_titles),
    ]:
        if not findings:
            continue
        lines.append(f"## {title}")
        lines.append("")
        for finding in findings[:WORST]:
            detail = f" — {finding.detail}" if finding.detail else ""
            # A finding with no note is one the backend could not place: the
            # Obsidian CLI names a broken target without saying which note
            # holds it. An empty wikilink would be worse than saying so.
            if finding.note:
                lines.append(f"- [[{_link(finding.note)}]]{detail}")
            else:
                lines.append(f"-{detail} — note not reported by this backend")
        if len(findings) > WORST:
            lines.append(f"- …and {len(findings) - WORST} more")
        lines.append("")

    if report.leftovers or report.empty_daily:
        lines.append("## Leftovers")
        lines.append("")
        lines.append("Empty, unnamed, or too short to be a note. Tiro lists these and "
                     "nothing more; tag one `tiro: triage` to have it proposed for "
                     "an archive folder, or deal with it yourself.")
        lines.append("")
        if report.empty_daily:
            lines.append(f"- {report.empty_daily} empty daily note(s), created on open "
                         "and never written in")
        for finding in report.leftovers[:WORST * 2]:
            detail = f" — {finding.detail}" if finding.detail else ""
            lines.append(f"- {finding.kind}: [[{_link(finding.note)}]]{detail}")
        if len(report.leftovers) > WORST * 2:
            lines.append(f"- …and {len(report.leftovers) - WORST * 2} more")
        lines.append("")

    if report.orphans:
        lines.append("## Orphans")
        lines.append("")
        lines.append("Nothing links to these. Not a problem in itself — some notes "
                     "are meant to stand alone. Daily notes are not counted.")
        lines.append("")
        for rel in report.orphans[:WORST]:
            lines.append(f"- [[{_link(rel)}]]")
        if len(report.orphans) > WORST:
            lines.append(f"- …and {len(report.orphans) - WORST} more")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    _save_counts(config, report)
    return path


def _counts_path(config: Config) -> Path:
    return config.tiro_dir / "health.json"


def _previous_counts(config: Config) -> dict[str, int]:
    path = _counts_path(config)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("counts", {})
    except json.JSONDecodeError:
        return {}


def _save_counts(config: Config, report: Report) -> None:
    path = _counts_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"when": report.when, "counts": report.counts}, indent=2) + "\n",
        encoding="utf-8",
    )


def _link(rel: str) -> str:
    return rel[:-3] if rel.endswith(".md") else rel
