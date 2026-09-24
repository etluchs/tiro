"""Reflect, accept, reject: rules that change by proposal only (DESIGN §7).

``reflect`` reads the correction log and, where the user has overruled Tiro's
filing the same way often enough, writes a proposed rule. ``accept`` appends it
to ``.tiro/rules.md``; ``reject`` records why not, so it is never proposed
again. Tiro never edits ``rules.md`` any other way.

**Boring on purpose.** A proposal says what the log shows and nothing more:
"notes Tiro proposed for `Areas/Didaktik/` you filed in `Teaching/HS26/`",
with every correction behind it listed by date and note. It does not guess at
the concept behind the pattern ("course material goes under Teaching"), because
a generalisation the user did not make is exactly the plausible nonsense a
learning loop must not produce. The user sharpens the wording if they want to;
it is a plain markdown file.

What counts as evidence:

- Only filing corrections: ``overrode-proposal`` (the user changed a proposed
  destination) and ``moved-after-filing`` (the user moved a note Tiro filed).
  Each is a direction, from the folder Tiro chose to the folder the user chose.
  A ``rejected-block`` says the user disliked some output, not where anything
  belongs, so it is counted in the report and never turned into a rule.
- One note counts once per direction, however often it was corrected.
- A direction with *any* evidence against it — the user also moved notes the
  other way — is held back and shown as such. Opposing corrections are not
  a rule; they are an open question.
- A direction that was ever proposed is never proposed again, whatever became
  of it. Rejection is final until the user deletes the registry entry.

The threshold is configurable (``[reflect] threshold``) and recorded on every
proposal, because three is a guess and the evidence will say what it should be.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from tiro import corrections as corrections_mod
from tiro.config import Config
from tiro.corrections import Correction
from tiro.journal import HEADER

FILING_KINDS = ("overrode-proposal", "moved-after-filing")
_RULE_HEADING = re.compile(r"^###\s+(R-(\d+))\b(.*)$", re.M)


def folder_of(rel: str) -> str:
    """The folder a note sits in, as a vault-relative path; "" for the root."""
    parent = PurePosixPath(rel).parent.as_posix()
    return "" if parent == "." else parent


def label(folder: str) -> str:
    return f"`{folder}/`" if folder else "the vault root"


@dataclass(frozen=True)
class Direction:
    src: str  # the folder Tiro chose
    dst: str  # the folder the user chose instead

    @property
    def reverse(self) -> "Direction":
        return Direction(self.dst, self.src)


@dataclass
class Proposal:
    id: str
    src: str
    dst: str
    status: str  # pending | accepted | rejected
    threshold: int
    created: str
    corrections: list[dict] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    decided: str = ""
    reason: str = ""

    @property
    def direction(self) -> Direction:
        return Direction(self.src, self.dst)

    @property
    def title(self) -> str:
        return f"Notes Tiro proposed for {label(self.src)} go in {label(self.dst)}"


@dataclass
class Report:
    new: list[Proposal] = field(default_factory=list)
    held_back: list[tuple[Direction, int, int]] = field(default_factory=list)
    below_threshold: list[tuple[Direction, int]] = field(default_factory=list)
    rejected_blocks: int = 0


# -- evidence --------------------------------------------------------------


def directions(log: list[Correction]) -> dict[Direction, list[Correction]]:
    """Filing corrections grouped by direction, one per note per direction.

    A rename inside the same folder is not a filing correction: the user kept
    Tiro's folder and changed the name.
    """
    grouped: dict[Direction, dict[str, Correction]] = {}
    for c in log:
        if c.kind not in FILING_KINDS:
            continue
        d = Direction(folder_of(c.was), folder_of(c.now))
        if d.src == d.dst:
            continue
        grouped.setdefault(d, {})[c.note_id or c.now] = c
    return {d: sorted(by_note.values(), key=lambda c: c.when)
            for d, by_note in grouped.items()}


# -- the registry ----------------------------------------------------------


def _registry_path(config: Config) -> Path:
    return config.tiro_dir / "proposals" / "rules.json"


def load(config: Config) -> list[Proposal]:
    path = _registry_path(config)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    known = set(Proposal.__dataclass_fields__)
    return [Proposal(**{k: v for k, v in item.items() if k in known}) for item in raw]


def save(config: Config, proposals: list[Proposal]) -> None:
    path = _registry_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(p) for p in proposals], indent=2) + "\n",
                    encoding="utf-8")


def find(config: Config, rule_id: str) -> tuple[list[Proposal], Proposal]:
    proposals = load(config)
    for p in proposals:
        if p.id.lower() == rule_id.strip().lower():
            return proposals, p
    raise KeyError(f"no proposal {rule_id}; `tiro reflect` lists what there is")


def _rules_path(config: Config) -> Path:
    return config.tiro_dir / "rules.md"


def _next_id(config: Config, proposals: list[Proposal]) -> str:
    rules = _rules_path(config)
    numbers = [int(m.group(2)) for m in _RULE_HEADING.finditer(
        rules.read_text(encoding="utf-8") if rules.exists() else "")]
    numbers += [int(p.id.split("-")[1]) for p in proposals if p.id.startswith("R-")]
    return f"R-{max(numbers, default=0) + 1:03d}"


def _conflicts(config: Config, d: Direction) -> list[str]:
    """Rules in ``rules.md`` that mention either folder. Not proof of a
    conflict — a rule may agree — but the user should read them together
    before accepting, so the proposal names them."""
    rules = _rules_path(config)
    if not rules.exists():
        return []
    text = rules.read_text(encoding="utf-8")
    heads = list(_RULE_HEADING.finditer(text))
    found = []
    for i, head in enumerate(heads):
        section = text[head.start(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        for folder in (d.src, d.dst):
            if folder and re.search(r"(?<![\w/])" + re.escape(folder) + r"(?![\w-])", section):
                found.append(head.group(1))
                break
    return found


# -- reflect ---------------------------------------------------------------


def reflect(config: Config, *, today: str, threshold: int | None = None) -> Report:
    """Read the log, write any new proposals, and redraw the board."""
    threshold = threshold or config.reflect.threshold
    log = corrections_mod.read(config)
    grouped = directions(log)
    proposals = load(config)
    ever_proposed = {p.direction for p in proposals}
    report = Report(rejected_blocks=sum(1 for c in log if c.kind == "rejected-block"))

    for d, evidence in sorted(grouped.items(), key=lambda kv: (kv[0].src, kv[0].dst)):
        if d in ever_proposed:
            continue
        against = grouped.get(d.reverse, [])
        if against:
            report.held_back.append((d, len(evidence), len(against)))
            continue
        if len(evidence) < threshold:
            report.below_threshold.append((d, len(evidence)))
            continue
        proposal = Proposal(
            id=_next_id(config, proposals),
            src=d.src,
            dst=d.dst,
            status="pending",
            threshold=threshold,
            created=today,
            corrections=[{"kind": c.kind, "note": c.note, "was": c.was, "now": c.now,
                          "when": c.when[:10]} for c in evidence],
            conflicts=_conflicts(config, d),
        )
        proposals.append(proposal)
        report.new.append(proposal)

    save(config, proposals)
    for p in report.new:
        _write_proposal_file(config, p)
    write_board(config, proposals, report)
    return report


def due(config: Config, state: dict, today: str) -> bool:
    last = state.get("reflect", {}).get("last", "")
    if not last:
        return True
    from datetime import date

    return (date.fromisoformat(today) - date.fromisoformat(last)).days >= config.reflect.every_days


def mark_done(state: dict, today: str) -> None:
    state.setdefault("reflect", {})["last"] = today


# -- accept and reject -----------------------------------------------------


def rule_text(p: Proposal) -> str:
    """The section ``accept`` appends to ``rules.md``: numbered, dated, with its
    provenance and the notes that earned it (DESIGN §7)."""
    dates = ", ".join(sorted({c["when"] for c in p.corrections}))
    examples = ", ".join(f"`{c['now']}`" for c in p.corrections)
    lines = [
        f"### {p.id} — {p.title}",
        f"Since {p.decided}. Proposed by Tiro after {len(p.corrections)} corrections "
        f"({dates}), accepted by the user.",
        f"When a note looks like one Tiro would put in {label(p.src)}, consider "
        f"{label(p.dst)} first.",
        f"Examples: {examples}",
    ]
    return "\n".join(lines) + "\n"


def accept(config: Config, rule_id: str, *, today: str) -> Proposal:
    proposals, p = find(config, rule_id)
    if p.status != "pending":
        raise ValueError(f"{p.id} is already {p.status}")
    p.status, p.decided = "accepted", today
    rules = _rules_path(config)
    existing = rules.read_text(encoding="utf-8") if rules.exists() else (
        "# Filing rules for this vault\n\n"
        "Tiro proposes changes to this file; it never edits it without your accept.\n")
    rules.parent.mkdir(parents=True, exist_ok=True)
    rules.write_text(existing.rstrip("\n") + "\n\n" + rule_text(p), encoding="utf-8")
    save(config, proposals)
    _write_proposal_file(config, p)
    write_board(config, proposals)
    return p


def reject(config: Config, rule_id: str, reason: str, *, today: str) -> Proposal:
    proposals, p = find(config, rule_id)
    if p.status != "pending":
        raise ValueError(f"{p.id} is already {p.status}")
    if not reason.strip():
        raise ValueError("say why, so the reason is there when the evidence is re-read")
    p.status, p.decided, p.reason = "rejected", today, reason.strip()
    save(config, proposals)
    _write_proposal_file(config, p)
    write_board(config, proposals)
    return p


# -- what the user reads ---------------------------------------------------


def _evidence_lines(p: Proposal) -> list[str]:
    out = []
    for c in p.corrections:
        verb = "you changed the proposal" if c["kind"] == "overrode-proposal" else "you moved it"
        out.append(f"- {c['when']} — [[{c['now'][:-3] if c['now'].endswith('.md') else c['now']}]]: "
                   f"Tiro chose `{c['was']}`, {verb} to `{c['now']}`")
    return out


def _write_proposal_file(config: Config, p: Proposal) -> None:
    path = config.tiro_dir / "proposals" / f"{p.id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = [f"# {p.id} — {p.title}", "",
            f"Status: **{p.status}**" + (f" on {p.decided}" if p.decided else ""),
            f"Proposed {p.created}, from {len(p.corrections)} corrections "
            f"(threshold {p.threshold}).", ""]
    if p.reason:
        body += [f"Rejected because: {p.reason}", ""]
    body += ["## Evidence", "", *_evidence_lines(p), ""]
    if p.conflicts:
        body += [f"Read with {', '.join(p.conflicts)} in `rules.md`, which mention "
                 "the same folders.", ""]
    body += ["## The rule, as it would be added", "", "```markdown", rule_text(
        p if p.decided else Proposal(**{**asdict(p), "decided": "<the day you accept>"})
    ).rstrip("\n"), "```", ""]
    path.write_text("\n".join(body), encoding="utf-8")


def write_board(config: Config, proposals: list[Proposal], report: Report | None = None) -> Path:
    """``Tiro/Proposals.md``: what is waiting on the user, and the history."""
    path = config.notes_dir / "Proposals.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = [p for p in proposals if p.status == "pending"]
    decided = [p for p in proposals if p.status != "pending"]

    lines = ["---", "title: Tiro proposes", "---", "", HEADER, ""]
    if not pending:
        lines.append("Nothing waiting on you.")
    for p in pending:
        lines += [f"## {p.id} — {p.title}", "",
                  f"From {len(p.corrections)} corrections (threshold {p.threshold}):", "",
                  *_evidence_lines(p), ""]
        if p.conflicts:
            lines += [f"> [!warning] Read with {', '.join(p.conflicts)} first — "
                      "those rules mention the same folders.", ""]
        lines += [f"Accept: `tiro accept {p.id}` · Reject: `tiro reject {p.id} \"why\"`", ""]

    if report and (report.held_back or report.below_threshold or report.rejected_blocks):
        lines += ["", "## Seen, not proposed", ""]
        for d, n_for, n_against in report.held_back:
            lines.append(f"- {label(d.src)} → {label(d.dst)}: {n_for} for, {n_against} "
                         "against. Held back: you have moved notes both ways.")
        for d, n in report.below_threshold:
            lines.append(f"- {label(d.src)} → {label(d.dst)}: {n} so far, "
                         f"{config.reflect.threshold} needed.")
        if report.rejected_blocks:
            lines.append(f"- {report.rejected_blocks} block(s) of Tiro's deleted. That says "
                         "some output was unwanted, not where anything belongs, so it "
                         "makes no rule.")
    if decided:
        lines += ["", "## Decided", ""]
        for p in sorted(decided, key=lambda p: p.id):
            why = f" — {p.reason}" if p.reason else ""
            lines.append(f"- {p.id} {p.status} {p.decided}: {p.title}{why}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
