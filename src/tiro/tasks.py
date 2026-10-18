"""Moving open tasks to the note they belong in (docs/TASK-MOVES.md).

Tasks get written down wherever the user happens to be, usually the day's
daily note, and stay there. Triage can tell where one belongs; this module is
how it gets there without the user copying lines by hand.

Three steps, and the second is the user's unless Tiro is sure:

1. **Triage proposes.** For every open task in the note it names an existing
   note, or says the task stays and why. The runner checks each destination
   and writes the proposals as a block of its own, ``tasks``, one checkbox per
   move. Nothing moves.
2. **The user ticks.** One box per move, in the note. Ticking one accepts that
   move and no other.
3. **The runner moves.** No model: a ticked box is a mechanical instruction,
   carried out by code that checks the task is still there exactly as
   proposed, and that the destination has not changed since. The task leaves
   the source byte for byte and joins the destination's first task list. Then
   a check independent of the move itself confirms that the source lost
   exactly those lines and the destination gained exactly those lines, and
   nothing else changed on either side. That is the exception to never #3:
   whole task lines, verbatim, after an explicit accept, by the runner.

**Sure means checkable.** Triage may say it is sure of a destination and give
a basis: the task links to the note, carries a tag the note uses, names the
note, or a rule in ``rules.md`` covers it. When the runner confirms the basis
itself, and the note the task leaves is in an L4 folder, the move is made in
the same run with no tick: the L4 is the accept, given in advance, as for auto
mode's filing. Anything short of that is a proposal.

Wherever a task left, a small block of Tiro's stays in its place, linking to
where it went.

Everything here is text in, text out. The writes, the gate and the commit are
the runner's (``runner.move_tasks``).
"""

from __future__ import annotations

import difflib
import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, unquote

from tiro import gate, protocol
from tiro.config import Config, as_vault_path, in_folder
from tiro.links import _aliases, iter_links
from tiro.scan import OURS, Skipped, is_daily, iter_notes

#: The job and block name. Not a verb: the user never writes it.
JOB = "tasks"
#: Removing a line from the note the task leaves. The tick is the accept, as
#: ``tiro: file`` is for a note, so this is only "Tiro may write here at all":
#: the level its block already needed.
FROM_TRUST = "L2"
#: Putting a line into another note is putting something somewhere, which is
#: what L3 opens up, as it does for filing a note into a folder.
TO_TRUST = gate.MOVE_TO_TRUST
#: Moving a task with no tick: the folder it leaves must be L4, the level a
#: user raises a folder to when Tiro may move things out of it unasked.
SURE_FROM_TRUST = "L4"
#: The shortest name a "the task names the note" basis may go by.
NAME_AT_LEAST = 4
#: How many of its own landings Tiro remembers per destination.
LANDINGS_KEPT = 50
#: The small block left where a task was, linking to where it went.
TRACE = "moved"

# Any checkbox item, with what precedes it: a quote or callout prefix, an
# indent, and the marker. Group 3 is the status character.
_ANY_TASK = re.compile(r"^((?:[ \t]*>)*)([ \t]*)([-*+]|\d+[.)])[ \t]+\[(.)\](?=[ \t]|$)")
# A task at the top level of a list: column 0, a bullet, one space.
_TASK = re.compile(r"^([-*+]) \[(.)\](?=[ \t]|$)")
_BULLET = re.compile(r"^([-*+])[ \t]")
_FENCE = re.compile(r"^\s*(```|~~~)")
# An Obsidian block id at the end of a line. Other notes may link to it, and
# the link would follow the note, not the task.
_BLOCK_REF = re.compile(r"(?:^|\s)\^[A-Za-z0-9-]+\s*$")
# The Tasks plugin's fields: dates, priority, recurrence, ids. Cut from the
# text shown in the block, so that the plugin does not read Tiro's line as
# carrying the task's due date. The moved task keeps all of them.
_FIELDS = re.compile("[\U0001F4C5⏳\U0001F6EB➕✅❌\U0001F501"
                     "⏫\U0001F53C\U0001F53D⏬\U0001F53A\U0001F194⛔]")
# What may start a new block after a list item, rather than continue it.
_NEW_BLOCK = re.compile(r"^(?:[-*+][ \t]|\d+[.)][ \t]|#|>|```|~~~|<!--|\||---|\*\*\*|___)")

_MOVE = re.compile(r"^- \[(.)\] (.*?)<!-- tiro:move ([^>]*?) -->")
_MOVE_LINK = re.compile(r"\[\[([^\[\]|#^]+)")
_SECTIONS = {"**Stays here**": "stays", "**Moved**": "moved", "**Not moved**": "dropped"}


# -- the tasks in a note ----------------------------------------------------


@dataclass(frozen=True)
class Task:
    """An open or finished task in the user's prose, with everything under it."""

    start: int  # line index of the checkbox line
    end: int  # one past its last line: sub-items and indented continuation
    lines: tuple[str, ...]
    status: str  # the character between the brackets
    #: Why the runner will not move it, or "".
    problem: str = ""

    @property
    def open(self) -> bool:
        return self.status == " "

    @property
    def key(self) -> str:
        """The task's identity: every byte of it. Edited in any way since the
        proposal, it is a different task, and the proposal no longer holds."""
        return hashlib.sha256("\n".join(self.lines).encode("utf-8")).hexdigest()[:12]

    @property
    def text(self) -> str:
        return describe(self.lines[0])


def describe(line: str) -> str:
    """A task's words, for the block: no checkbox, no Tasks fields, no block id,
    and nothing that could close an HTML comment."""
    m = _ANY_TASK.match(line)
    rest = line[m.end():] if m else line
    cut = _FIELDS.search(rest)
    if cut:
        rest = rest[:cut.start()]
    rest = _BLOCK_REF.sub("", rest).replace("<!--", "").replace("-->", "")
    return " ".join(rest.split()) or "(a task with no words)"


def _prose(lines: list[str], text: str) -> list[bool]:
    """Which lines are the user's prose: not frontmatter, not a Tiro block,
    not fenced code. A task in any of those is not a task to move."""
    mask = [True] * len(lines)
    fm, _, present = protocol.split_frontmatter(text)
    if present:
        for i in range(min(len(fm) + 2, len(lines))):
            mask[i] = False
    for block in protocol.find_blocks(text):
        for i in range(block.start, block.end + 1):
            mask[i] = False
    fence: str | None = None
    for i, line in enumerate(lines):
        if not mask[i]:
            continue
        m = _FENCE.match(line)
        if m:
            if fence is None:
                fence = m.group(1)
            elif line.strip().startswith(fence):
                fence = None
            mask[i] = False
        elif fence is not None:
            mask[i] = False
    return mask


def _extent(lines: list[str], mask: list[bool], start: int) -> int:
    """One past the last line of the list item at ``start``: every indented
    line under it, and blank lines that have more of the item after them.
    Trailing blank lines are not the item's."""
    end = i = start + 1
    while i < len(lines) and mask[i]:
        line = lines[i]
        if not line.strip():
            j = i
            while j < len(lines) and mask[j] and not lines[j].strip():
                j += 1
            if j < len(lines) and mask[j] and lines[j][:1] in (" ", "\t"):
                i = j
                continue
            break
        if line[:1] not in (" ", "\t"):
            break
        end = i = i + 1
    return end


def tasks(text: str) -> list[Task]:
    """Every task in the user's prose that a person would see as one, in order.

    A top-level task carries everything indented under it, nested tasks
    included: those travel with it and are not listed again. Any other task,
    finished or open, is listed with the reason it cannot move, so that the
    block can say so rather than pass over it.
    """
    lines = text.split("\n")
    mask = _prose(lines, text)
    out: list[Task] = []
    inside = 0  # one past the last line of the current top-level task
    for i, line in enumerate(lines):
        if not mask[i] or i < inside:
            continue
        m = _ANY_TASK.match(line)
        if not m:
            continue
        quoted, indent, marker, status = m.groups()
        if quoted:
            out.append(Task(i, i + 1, (line,), status,
                            "it is inside a quote or callout"))
            continue
        if indent:
            out.append(Task(i, i + 1, (line,), status,
                            "it belongs to the item above it, and Tiro moves "
                            "only tasks at the top level of a list"))
            continue
        if not _TASK.match(line):
            out.append(Task(i, i + 1, (line,), status,
                            "it is a numbered item, and moving it would "
                            "renumber its list" if marker[0].isdigit()
                            else "its checkbox is not written `- [ ]`"))
            continue
        end = _extent(lines, mask, i)
        inside = end
        chunk = tuple(lines[i:end])
        problem = ""
        if any(_BLOCK_REF.search(l) for l in chunk):
            problem = "it has a block id, and links to it would break"
        elif (end < len(lines) and mask[end] and lines[end].strip()
              and not _NEW_BLOCK.match(lines[end])):
            problem = ("the line after it runs on without a blank line or an "
                       "indent, and Tiro cannot tell whether it is part of the task")
        out.append(Task(i, end, chunk, status, problem))
    return out


# -- where a task may go ----------------------------------------------------


def destination_problem(config: Config, source: str, to: str) -> str | None:
    """Why a task from ``source`` may not move to ``to``, or None.

    Checked when the move is proposed and again when it is carried out: the
    destination comes from the model, which read the note, which is the
    channel the constitution warns about.
    """
    problem = gate.destination_problem(config, to)
    if problem:
        return f"it is {problem}"
    to = as_vault_path(to)
    if to == source:
        return "it is this note"
    if not (config.vault / to).is_file():
        return "there is no such note, and Tiro moves tasks only into notes that exist"
    if is_daily(to):
        return "it is a daily note, and tasks never move into one"
    if in_folder(to, OURS):
        return "it is one of Tiro's own notes"
    for folder in config.tasks.never_into:
        if in_folder(to, folder):
            return f"`{folder}` is closed to moved tasks (`[tasks] never_into`)"
    if not config.trust.permits(to, TO_TRUST):
        return (f"its folder is {config.trust.level_for(to)}, and a task moves "
                f"only into an {TO_TRUST} folder")
    return None


def source_permits(config: Config, rel: str, text: str) -> bool:
    """May a task leave this note? Where Tiro may write its block: a tagged
    note counts its tag, as everywhere; an untagged one has its folder."""
    if protocol.verb(text) is not None:
        return gate.note_permits(config, rel, FROM_TRUST)
    return config.trust.permits(rel, FROM_TRUST)


def resolve(config: Config, index, value: str) -> str:
    """A destination as the model or the user named it: a path, with or
    without ``.md``, or a link target. Returns a vault path, or the value as
    given when nothing resolves, for the caller to refuse by name."""
    value = as_vault_path(str(value or "").strip())
    if value.startswith("[[") and value.endswith("]]"):
        value = value[2:-2].split("|")[0]
    if not value:
        return ""
    found = index.resolve(value[:-3] if value.endswith(".md") else value)
    if found and found.endswith(".md"):
        return found
    return value if value.endswith(".md") else value + ".md"


def link(index, rel: str) -> str:
    """The shortest wikilink that reaches ``rel``: its name when that is
    unique, its path otherwise."""
    stem = Path(rel).stem
    if index.resolve(stem) == rel:
        return f"[[{stem}]]"
    return f"[[{rel[:-3]}|{stem}]]"


# -- the block --------------------------------------------------------------


@dataclass
class Move:
    """A proposed move: one checkbox in the block."""

    task: str  # Task.key
    to: str  # the destination, a vault path
    hash: str  # the destination's user hash when proposed
    text: str  # the task's words
    ticked: bool = False
    note: str = ""  # one line under it: why it did not move last time
    #: the target of the link the line shows, which the user may have edited.
    named: str = ""
    #: the checked basis, when Tiro ticked this itself because it was sure.
    auto: str = ""


@dataclass
class Board:
    """The ``tasks`` block, parsed: proposals, what stays, what moved."""

    moves: list[Move] = field(default_factory=list)
    stays: list[str] = field(default_factory=list)
    moved: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


def block_of(text: str) -> protocol.Block | None:
    for block in protocol.find_blocks(text):
        if block.job == JOB:
            return block
    return None


def read_board(text: str) -> Board | None:
    """The note's tasks block, or None if it has none."""
    block = block_of(text)
    if block is None:
        return None
    board = Board()
    section = ""
    for line in block.body.split("\n"):
        if line.strip() in _SECTIONS:
            section = _SECTIONS[line.strip()]
            continue
        m = _MOVE.match(line)
        if m:
            attrs = dict(a.split("=", 1) for a in m.group(3).split() if "=" in a)
            board.moves.append(Move(
                task=attrs.get("task", ""), to=unquote(attrs.get("to", "")),
                hash=attrs.get("hash", ""), text=_after_link(m.group(2)),
                ticked=m.group(1) in "xX", named=visible_target(m.group(2)),
                auto=unquote(attrs.get("auto", ""))))
            section = "moves"
            continue
        if section == "moves" and line.startswith("  ") and board.moves:
            board.moves[-1].note = line.strip().strip("*")
            continue
        if section in ("stays", "moved", "dropped") and line.startswith("- "):
            getattr(board, section).append(line[2:])
    return board


def _after_link(shown: str) -> str:
    """The task's words from a move line: what follows "Move to [[…]]: "."""
    _, sep, rest = shown.partition("]]: ")
    return (rest if sep else shown).strip()


def visible_target(line_text: str) -> str:
    m = _MOVE_LINK.search(line_text)
    return m.group(1).strip() if m else ""


def move_line(move: Move, index) -> list[str]:
    box = "x" if move.ticked else " "
    meta = f"task={move.task} to={quote(move.to)} hash={move.hash}"
    if move.auto:
        meta += f" auto={quote(move.auto)}"
    out = [f"- [{box}] Move to {link(index, move.to)}: {move.text} <!-- tiro:move {meta} -->"]
    if move.note:
        out.append(f"  *{move.note}*")
    return out


def render(board: Board, index, today: str) -> str:
    waiting = len(board.moves)
    if waiting:
        head = (f"{_count(waiting)} belong{'s' if waiting == 1 else ''} in another "
                "note. Tick a move and Tiro makes it on its next run; nothing "
                "moves without a tick.")
    elif board.moved:
        head = f"Moved {_count(len(board.moved))} out of this note."
    else:
        head = "Every open task here stays."
    lines = [f"> [!abstract] Tiro · tasks · {today}", f"> {head}"]
    if board.moves:
        lines.append("")
        for move in board.moves:
            lines += move_line(move, index)
    for title, section in _SECTIONS.items():
        items = getattr(board, section)
        if items:
            lines += ["", title, *(f"- {item}" for item in items)]
    return "\n".join(lines)


def untick(text: str, why: str, index, today: str) -> str | None:
    """The note with every ticked move unticked and ``why`` under each, or
    None if nothing was ticked."""
    block = block_of(text)
    board = read_board(text)
    if block is None or board is None or not any(m.ticked for m in board.moves):
        return None
    for move in board.moves:
        if move.ticked:
            move.ticked = False
            move.auto = ""
            move.note = why
    return protocol.upsert_block(text, job=JOB, id=block.id,
                                 body=render(board, index, today))


def _count(n: int) -> str:
    return "1 task" if n == 1 else f"{n} tasks"


def propose(config: Config, rel: str, text: str, answers: list[dict], *,
            today: str, index, state: dict | None = None) -> str | None:
    """The tasks block a triage of ``rel`` leaves, or None to leave none.

    Every open task gets a line: a move to an existing note that passes every
    check, or the reason it stays. ``answers`` is triage's, by the number the
    runner gave each task; the runner, not the model, decides what reaches the
    block. A move already ticked and not yet made is kept, and so is the record
    of what moved before.

    A move triage is sure of, on a basis the runner confirms, is written
    ticked: the runner makes it in the same run (``sure_problem``).
    """
    found = [t for t in tasks(text) if t.open]
    old = read_board(text)
    if not found and old is None:
        return None
    movable = [t for t in found if not t.problem]
    by_number: dict[int, dict] = {}
    for answer in answers:
        try:
            by_number[int(answer.get("n"))] = answer
        except (TypeError, ValueError):
            continue

    board = Board(moved=list(old.moved) if old else [])
    present = Counter(t.key for t in movable)
    for move in old.moves if old else []:
        if move.ticked and present[move.task]:
            board.moves.append(move)
            present[move.task] -= 1
    kept = Counter(m.task for m in board.moves)
    for n, task in enumerate(movable, start=1):
        if kept[task.key]:
            kept[task.key] -= 1
            continue
        answer = by_number.get(n, {})
        why = " ".join(str(answer.get("why") or "").split())[:200]
        to = resolve(config, index, answer.get("to") or "")
        if not to:
            board.stays.append(f"{task.text} — {why or 'triage named no note for it'}")
            continue
        problem = destination_problem(config, rel, to)
        if problem:
            board.stays.append(f"{task.text} — not to `{to}`: {problem}")
            continue
        dest = (config.vault / to).read_text(encoding="utf-8")
        move = Move(task.key, to, protocol.user_hash(dest), task.text)
        if answer.get("sure") is True:
            basis = answer.get("basis") if isinstance(answer.get("basis"), dict) else {}
            why_not = sure_problem(config, state or {}, rel, task, to, basis, index)
            if why_not is None:
                move.ticked, move.auto = True, sure_text(basis)
            else:
                move.note = f"Not moved unasked: {why_not}."
        board.moves.append(move)
    for task in found:
        if task.problem:
            board.stays.append(f"{task.text} — {task.problem}")
    return render(board, index, today)


def sure_problem(config: Config, state: dict, source: str, task: Task, to: str,
                 basis: dict, index) -> str | None:
    """Why Tiro may not move this task without a tick, or None if it may.

    The model's "sure" is needed and never enough: the runner confirms the
    basis itself, from the task and the destination as they are.
    """
    if not config.trust.permits(source, SURE_FROM_TRUST):
        return (f"this note's folder is {config.trust.level_for(source)}, and Tiro "
                f"moves tasks unasked only out of {SURE_FROM_TRUST} folders")
    if [task.key, to] in state.get("tasks", {}).get("declined", []):
        return "you undid this move before"
    body = "\n".join(task.lines)
    dest = (config.vault / to).read_text(encoding="utf-8")
    if basis.get("link"):
        if any(index.resolve(l.target, from_rel=source) == to for l in iter_links(body)):
            return None
        return "the task does not link to that note"
    if basis.get("tag"):
        tag = "#" + str(basis["tag"]).strip().lstrip("#")
        if not _has_tag(body, tag):
            return f"the task does not carry {tag}"
        if not (_has_tag(_prose_only(dest), tag) or tag[1:].lower() in _fm_tags(dest)):
            return f"that note does not use {tag}"
        return None
    if basis.get("named"):
        name = " ".join(str(basis["named"]).split())
        if len(name) < NAME_AT_LEAST:
            return f"“{name}” is too short a name to go by"
        # Words, not tags: a tag is a basis of its own, checked against the
        # destination's use of it.
        if not _has_words(re.sub(r"#[^\s#]+", " ", task.text), name):
            return f"the task does not say “{name}”"
        if not any(_has_words(title, name) for title in (Path(to).stem, *_aliases(dest))):
            return f"that note is not called “{name}”"
        return None
    if basis.get("rule"):
        rule = str(basis["rule"]).strip()
        rules = config.tiro_dir / "rules.md"
        if re.fullmatch(r"R-\d+", rule) and rules.exists() and re.search(
                rf"(?<![\w-]){re.escape(rule)}(?!\d)", rules.read_text(encoding="utf-8")):
            return None
        return f"there is no rule {rule} in rules.md"
    return "triage gave no basis Tiro can check"


def sure_text(basis: dict) -> str:
    """The basis of an unasked move, as the note says it."""
    if basis.get("link"):
        return "the task links to it"
    if basis.get("tag"):
        return "by #" + str(basis["tag"]).strip().lstrip("#")
    if basis.get("named"):
        return "the task names it"
    return f"by {str(basis.get('rule', '')).strip()}"


def _has_tag(text: str, tag: str) -> bool:
    return bool(re.search(rf"(?<![\w/#]){re.escape(tag)}(?![\w/-])", text, re.I))


def _has_words(text: str, words: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(words)}(?!\w)", text, re.I))


def _fm_tags(text: str) -> set[str]:
    """The note's frontmatter tags, lower case, without the ``#``."""
    fm, _, present = protocol.split_frontmatter(text)
    out: set[str] = set()
    collecting = False
    for line in fm if present else []:
        if re.match(r"^tags?\s*:", line):
            value = line.split(":", 1)[1].strip()
            collecting = not value
            out |= {v.strip(" \"'#").lower() for v in re.split(r"[,\s]+", value.strip("[]")) if v.strip()}
            continue
        if collecting and line.startswith((" ", "\t", "-")):
            out.add(line.lstrip(" \t-").strip(" \"'#").lower())
            continue
        collecting = False
    return out - {""}


def declined(state: dict, task: str, to: str) -> None:
    """The user undid an unasked move: never make that one unasked again."""
    pairs = state.setdefault("tasks", {}).setdefault("declined", [])
    if [task, to] not in pairs:
        pairs.append([task, to])


# -- moving -----------------------------------------------------------------


def remove(text: str, chunks: list[Task], trace=None) -> str:
    """The note without these tasks: their lines, and nothing else of the
    user's. ``trace``, if given, makes the lines of a Tiro block to leave in
    each task's place (``trace_block``)."""
    lines = text.split("\n")
    for task in sorted(chunks, key=lambda t: t.start, reverse=True):
        if tuple(lines[task.start:task.end]) != task.lines:
            raise ValueError(f"the task at line {task.start + 1} is not where it was read")
        lines[task.start:task.end] = trace(task) if trace else []
    return "\n".join(lines)


def trace_block(note_id: str, task: Task, where: str) -> list[str]:
    """What stays where a task was: a block of Tiro's, so not the user's prose,
    with one line saying where the task went. Not a checkbox: the plugin
    must not count it as a task."""
    bid = f"{note_id}-{TRACE}-{task.key[:8]}"
    return [f"<!-- tiro:begin job={TRACE} id={bid} -->",
            f"- ↗ moved to {where}: {task.text}",
            f"<!-- tiro:end id={bid} -->"]


def insert(text: str, chunk: list[str]) -> str:
    """The note with ``chunk`` added to its task list.

    After the last task of the first task list in the note. With no task list,
    at the end of the note's prose, before any blocks of Tiro's that close it,
    set apart by a blank line. Nothing else changes.
    """
    lines = text.split("\n")
    mask = _prose(lines, text)
    at = _after_first_task_list(lines, mask)
    new = list(chunk)
    if at is None:
        at = _end_of_prose(lines, text)
        if at > 0 and lines[at - 1].strip():
            new = ["", *new]
        if at < len(lines) and lines[at].strip():
            new = [*new, ""]
    return "\n".join(lines[:at] + new + lines[at:])


def _after_first_task_list(lines: list[str], mask: list[bool]) -> int | None:
    first = next((i for i, l in enumerate(lines) if mask[i] and _TASK.match(l)), None)
    if first is None:
        return None
    marker = lines[first][0]
    last = j = _extent(lines, mask, first)
    while True:
        k = j
        while k < len(lines) and mask[k] and not lines[k].strip():
            k += 1
        if k >= len(lines) or not mask[k]:
            return last
        m = _BULLET.match(lines[k])
        if not m or m.group(1) != marker:
            return last
        j = _extent(lines, mask, k)
        if _TASK.match(lines[k]):
            last = j


def _end_of_prose(lines: list[str], text: str) -> int:
    blocks = {b.end: b for b in protocol.find_blocks(text)}
    end = len(lines)
    while True:
        while end > 0 and not lines[end - 1].strip():
            end -= 1
        block = blocks.get(end - 1)
        if block is None:
            return end
        end = block.start


def check(source_before: str, source_after: str, chunks: list[list[str]],
          dest_before: dict[str, str], dest_after: dict[str, str],
          into: dict[str, list[list[str]]]) -> list[str]:
    """Did the move do exactly what was accepted, and nothing else?

    Independent of how the move was made: it compares the notes before and
    after. The source's prose may only have lost lines, and exactly the moved
    ones; Tiro's block and keys there are not prose. Each destination may only
    have gained lines, exactly the ones that moved into it, give or take a
    blank line around them. This is what makes never #3's exception a fact
    rather than a promise.
    """
    failures: list[str] = []
    lost, gained = _diff(_prose_only(source_before), _prose_only(source_after))
    if gained:
        failures.append("the source gained text it should not have")
    if Counter(lost) != Counter(l for c in chunks for l in c):
        failures.append("the source lost text other than the moved tasks")
    for rel, before in dest_before.items():
        lost, gained = _diff(before, dest_after.get(rel, before))
        if lost:
            failures.append(f"{rel} lost text; a move only adds")
        moved = [l for c in into.get(rel, []) for l in c if l.strip()]
        if Counter(l for l in gained if l.strip()) != Counter(moved):
            failures.append(f"{rel} gained text other than the moved tasks")
    return failures


def _prose_only(text: str) -> str:
    return protocol.strip_blocks(protocol.strip_keys(text))


def _diff(before: str, after: str) -> tuple[list[str], list[str]]:
    a, b = before.split("\n"), after.split("\n")
    lost: list[str] = []
    gained: list[str] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op in ("delete", "replace"):
            lost += a[i1:i2]
        if op in ("insert", "replace"):
            gained += b[j1:j2]
    return lost, gained


# -- which notes, and Tiro's own changes ------------------------------------


def ticked(text: str) -> bool:
    board = read_board(text) if "tiro:move" in text else None
    return bool(board and any(m.ticked for m in board.moves))


def accepted(config: Config, *, now: float, only: set[str] | None = None
             ) -> tuple[list[str], list[Skipped]]:
    """Notes with a ticked move waiting. The tick is in Tiro's block, which is
    outside the user hash, so this is a trigger of its own; carrying the move
    out unticks it, so it fires once."""
    found: list[str] = []
    skipped: list[Skipped] = []
    for path in iter_notes(config.vault):
        rel = path.relative_to(config.vault).as_posix()
        if only is not None and rel not in only:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not ticked(text):
            continue
        if config.trust.level_for(rel) == "L0":
            skipped.append(Skipped(rel, "tasks: in an L0 folder; Tiro does not write there"))
            continue
        age = now - path.stat().st_mtime
        if only is None and age < config.run.skip_recent_seconds:
            skipped.append(Skipped(rel, f"tasks: ticked {age:.0f}s ago; you may still be ticking"))
            continue
        found.append(rel)
    return found, skipped


def remember_landing(state: dict, rel: str, before: str, after: str) -> None:
    """Record that Tiro turned ``rel``'s user hash from ``before`` into
    ``after`` by adding tasks to it, and did nothing else to it."""
    landed = state.setdefault("tasks", {}).setdefault("landed", {}).setdefault(rel, [])
    landed.append([before, after])
    del landed[:-LANDINGS_KEPT]


def seen_but_landed(state: dict, rel: str, text: str) -> bool:
    """Tiro has seen this note, and all that changed since is tasks it moved
    in. Not a change that wants a job: Tiro's own output must not trigger it."""
    recorded = protocol.read_keys(text).get("tiro/hash", "")
    current = protocol.user_hash(text)
    return bool(recorded) and recorded != current and only_landed(state, rel, recorded, current)


def tiro_left(state: dict, rel: str, current: str) -> bool:
    """Is ``current`` exactly what a landing of Tiro's left? Then a recent
    modification time is Tiro's own, and not the user typing."""
    pairs = state.get("tasks", {}).get("landed", {}).get(rel, [])
    return any(after == current for _, after in pairs)


def only_landed(state: dict, rel: str, since: str, now: str) -> bool:
    """Is ``now`` what ``since`` became through tasks Tiro moved in, and
    nothing else?

    A destination is not touched beyond the task, not even its ``tiro/hash``,
    so this is how a landing is told from the user editing the note. It keeps
    criterion 9 from refusing the second of two moves into one note, and keeps
    Tiro's own move from looking like a change that wants another job.
    """
    if since == now:
        return True
    pairs = state.get("tasks", {}).get("landed", {}).get(rel, [])
    seen, frontier = {since}, [since]
    while frontier:
        current = frontier.pop()
        for before, after in pairs:
            if before == current and after not in seen:
                if after == now:
                    return True
                seen.add(after)
                frontier.append(after)
    return False
