"""Tiro/ — the surface the user supervises from, without reading a diff."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from tiro import protocol
from tiro.config import Config
from tiro.scan import iter_notes

HEADER = "<!-- Written by Tiro. Edits here are overwritten. -->"


@dataclass
class Entry:
    verb: str
    note: str
    outcome: str  # done | blocked | needs-input | preview | skipped
    detail: str = ""
    commit: str = ""


@dataclass
class RunRecord:
    run_id: str
    started: str
    ops_backend: str
    finished: str = ""
    entries: list[Entry] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    tokens: int = 0

    def add(self, entry: Entry) -> None:
        self.entries.append(entry)

    def note_line(self, line: str) -> None:
        self.notes.append(line)


def new_run_id(now: datetime | None = None) -> str:
    import uuid

    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H-%MZ-") + uuid.uuid4().hex[:4]


def write_run_record(config: Config, record: RunRecord) -> Path:
    out = config.tiro_dir / "runs" / record.run_id
    out.mkdir(parents=True, exist_ok=True)
    path = out / "run.json"
    path.write_text(json.dumps(asdict(record), indent=2) + "\n", encoding="utf-8")
    return path


def write_journal(config: Config, record: RunRecord) -> Path:
    """One line per job, every note linked. If a day's work is not legible from
    here, the run did not really report itself."""
    day = record.run_id[:10]
    path = config.notes_dir / "Journal" / f"{day}.md"
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if not existing:
        existing = f"---\ntitle: Tiro — {day}\n---\n\n{HEADER}\n"

    lines = [f"\n## Run {record.run_id}", ""]
    lines.append(f"Backend: `{record.ops_backend}`. "
                 f"{len(record.entries)} job(s), {len(record.skipped)} skipped.")
    lines.append("")
    if not (record.entries or record.skipped or record.notes):
        lines.append("- nothing to do")
    for entry in record.entries:
        mark = {"done": "✓", "blocked": "✗", "needs-input": "?", "preview": "·"}.get(entry.outcome, "·")
        detail = f" — {entry.detail}" if entry.detail else ""
        lines.append(f"- {mark} `{entry.verb}` [[{_link(entry.note)}]]{detail}")
    for skip in record.skipped:
        lines.append(f"- · skipped [[{_link(skip['rel'])}]] — {skip['why']}")
    for note in record.notes:
        lines.append(f"- {note}")
    lines.append("")

    path.write_text(existing.rstrip("\n") + "\n" + "\n".join(lines), encoding="utf-8")
    return path


def write_questions(config: Config) -> Path:
    """Every note waiting on the user, in one place, so none is lost in a folder
    they never open."""
    rows: list[tuple[str, str]] = []
    for note in iter_notes(config.vault):
        rel = note.relative_to(config.vault).as_posix()
        if rel.startswith("Tiro/"):
            continue
        text = note.read_text(encoding="utf-8", errors="replace")
        keys = protocol.read_keys(text)
        if keys.get("tiro/status") != "needs-input":
            continue
        question = ""
        for block in protocol.find_blocks(text):
            for line in block.body.split("\n"):
                stripped = line.lstrip("> ").strip()
                if stripped and not stripped.startswith("[!"):
                    question = stripped
                    break
            if question:
                break
        rows.append((rel, question))

    path = config.notes_dir / "Questions.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = [f"---\ntitle: Tiro asks\n---\n\n{HEADER}\n"]
    if not rows:
        body.append("\nNothing waiting on you.\n")
    else:
        body.append(f"\n{len(rows)} note(s) waiting on you.\n")
        for rel, question in sorted(rows):
            body.append(f"- [[{_link(rel)}]] — {question or 'see the note'}")
        body.append("")
    path.write_text("\n".join(body), encoding="utf-8")
    return path


def _link(rel: str) -> str:
    return rel[:-3] if rel.endswith(".md") else rel
