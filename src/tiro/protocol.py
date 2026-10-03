"""The note protocol: frontmatter keys, Tiro-owned blocks, the user-content hash.

Two deliberate choices run through this module.

**Tiro never round-trips the user's YAML.** A YAML load/dump cycle reorders keys,
reflows lists, normalises quotes and drops comments — all of it showing up as
noise in a diff the user is supposed to be able to trust. So frontmatter is
edited as *text*: we insert, replace and remove only the lines whose key is
``tiro`` or starts with ``tiro/``, and every other byte of the block is passed
through untouched.

**The hash covers the user's content only.** Strip Tiro's own keys and blocks,
normalise whitespace, hash that. Tiro's output therefore cannot change the hash,
which is what stops the run loop from re-triggering on its own work. Everything
in section 4.2 of the design rests on this; see ``test_protocol.py``.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass

VERBS = ("triage", "file", "research", "distill", "spec", "dispatch", "connect")
#: ``tiro: hold`` is the user saying "leave this one alone". It is a value of
#: the user's key but not a job: the scan passes over it before a job exists,
#: so auto mode never looks at the note and nothing is queued for it.
HOLD = "hold"
WORDS = (*VERBS, HOLD)
#: Not a word the user writes: the job a free-form order runs as. Anything in
#: ``tiro:`` with a space in it is an order, and so is a ``> [!tiro]`` callout.
ORDER = "order"
STATUSES = ("queued", "working", "done", "blocked", "needs-input")

FENCE = "---"
_TIRO_KEY = re.compile(r"^(tiro(?:/[A-Za-z0-9_-]+)?)\s*:(.*)$")
#: ``#tiro/research`` is the documented form and is read anywhere in the body.
#: ``#tiro research`` is what people actually type at the end of a note, and is
#: read only when it *is* the end of a line — "#tiro file it tomorrow" is a
#: sentence, not a request to move the note. A ``#tiro`` followed by anything
#: else is a near-miss, and lint reports it.
_VERB_ALT = "|".join(WORDS)
_BODY_TAG = re.compile(r"(?<![\w/#])#tiro/(" + _VERB_ALT + r")(?![\w/-])", re.I)
_BODY_TAG_LOOSE = re.compile(r"(?<![\w/#])#tiro[ \t]+(" + _VERB_ALT + r")[.!]?[ \t]*$", re.M | re.I)
_BODY_TAG_ANY = re.compile(r"(?<![\w/#])#tiro(?!\w)", re.I)
_FENCED = re.compile(r"^\s*(```|~~~).*?^\s*\1", re.M | re.S)
_INLINE_CODE = re.compile(r"`[^`\n]*`")


def without_code(text: str) -> str:
    """The text with fenced blocks and inline code blanked.

    A tag inside code is an example, not a request. Lint's own report says
    "try `#tiro research`"; read literally, that line would queue lint's report
    as a research job every run.
    """
    return _INLINE_CODE.sub(" ", _FENCED.sub(" ", text))
_BLOCK_BEGIN = re.compile(r"^<!--\s*tiro:begin\s+job=(\S+)\s+id=(\S+)\s*-->\s*$")
_BLOCK_END = re.compile(r"^<!--\s*tiro:end\s+id=(\S+)\s*-->\s*$")


class ProtocolError(Exception):
    """The note does not conform. Always ends the job as blocked, never silently."""


@dataclass(frozen=True)
class Block:
    """A Tiro-owned region of a note."""

    job: str
    id: str
    start: int  # index of the begin marker line
    end: int  # index of the end marker line
    body: str


# --------------------------------------------------------------------------
# frontmatter
# --------------------------------------------------------------------------


def split_frontmatter(text: str) -> tuple[list[str], str, bool]:
    """Return (frontmatter lines, body, present).

    The fences themselves are not included. A note whose first line is not
    ``---`` has no frontmatter, which is legal and common.

    A leading byte-order mark, which some Windows editors write, is not part
    of the first line. Tiro drops it when it next writes the note.
    """
    text = text.lstrip("﻿")
    lines = text.split("\n")
    if not lines or lines[0].strip() != FENCE:
        return [], text, False
    for i in range(1, len(lines)):
        if lines[i].strip() in (FENCE, "..."):
            return lines[1:i], "\n".join(lines[i + 1 :]), True
    # An unterminated fence is malformed; treat the note as having no
    # frontmatter rather than swallowing the whole file.
    return [], text, False


def _join(fm: list[str], body: str) -> str:
    return "\n".join([FENCE, *fm, FENCE, body])


def _unquote(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def read_keys(text: str) -> dict[str, str]:
    """Every ``tiro`` / ``tiro/*`` key in the frontmatter, as strings.

    Later duplicates win, matching how YAML parsers generally behave. Values are
    returned unquoted and stripped; no other interpretation is attempted.
    """
    fm, _, present = split_frontmatter(text)
    if not present:
        return {}
    out: dict[str, str] = {}
    for line in fm:
        m = _TIRO_KEY.match(line)
        if m:
            out[m.group(1)] = _unquote(m.group(2))
    return out


def set_key(text: str, key: str, value: str) -> str:
    """Set a ``tiro*`` key, creating the frontmatter block if needed.

    Replaces the key in place when it exists, so its position in the user's
    property order is preserved.
    """
    _require_tiro_key(key)
    line = f"{key}: {value}"
    fm, body, present = split_frontmatter(text)
    if not present:
        # ``body`` rather than ``text``: the same bytes, minus a byte-order
        # mark that must not end up below the new fence.
        return _join([line], body if body.startswith("\n") else "\n" + body)
    for i, existing in enumerate(fm):
        m = _TIRO_KEY.match(existing)
        if m and m.group(1) == key:
            fm[i] = line
            return _join(fm, body)
    return _join([*fm, line], body)


def remove_key(text: str, key: str) -> str:
    _require_tiro_key(key)
    fm, body, present = split_frontmatter(text)
    if not present:
        return text
    kept = [l for l in fm if not (_TIRO_KEY.match(l) and _TIRO_KEY.match(l).group(1) == key)]
    return _join(kept, body)


#: Keys the user is expected to edit, and whose edits must therefore count as
#: an edit. The verb: changing ``tiro: triage`` to ``tiro: file`` is the accept
#: the filing flow waits for. The destination: "the note wins" over the skill's
#: answer, which is only true if editing it on the note is noticed. Tiro writes
#: both too, but always *before* it records the hash, so its own writes do not
#: loop.
USER_KEYS = frozenset({"tiro", "tiro/filed-to"})


def strip_keys(text: str, *, for_hash: bool = False) -> str:
    """Remove every ``tiro*`` key. Used by ``tiro strip``, and — with
    ``for_hash`` — for the hash, where the keys in ``USER_KEYS`` stay."""
    fm, body, present = split_frontmatter(text)
    if not present:
        return text
    kept = [l for l in fm
            if not (m := _TIRO_KEY.match(l)) or (for_hash and m.group(1) in USER_KEYS)]
    if not kept:
        # An empty frontmatter block is noise; drop the fences too.
        return body.lstrip("\n")
    return _join(kept, body)


def _require_tiro_key(key: str) -> None:
    if key != "tiro" and not key.startswith("tiro/"):
        raise ProtocolError(f"refusing to write non-Tiro frontmatter key {key!r}")


# --------------------------------------------------------------------------
# the request
# --------------------------------------------------------------------------


_ORDER_CALLOUT = re.compile(r"^[ \t]*>[ \t]*\[!tiro\][+-]?[ \t]*(.*)$", re.I)
_CALLOUT_LINE = re.compile(r"^[ \t]*>[ \t]?(.*)$")


def _is_order(value: str) -> bool:
    """A verb is one word. Anything longer is the user saying what they want."""
    return len(value.split()) > 1


def verb(text: str) -> str | None:
    """The requested job: a verb from the ``tiro:`` key or a ``#tiro/<verb>``
    body tag, or :data:`ORDER` for a free-form order.

    The frontmatter key wins when both are present. A single unrecognised word
    is returned as-is so the caller can block the note with a useful message
    rather than silently ignoring a typo: ``tiro: reserch`` is almost always a
    misspelt verb, not an order, and saying so costs no model time.
    """
    keys = read_keys(text)
    if "tiro" in keys and keys["tiro"]:
        value = keys["tiro"].strip()
        return ORDER if _is_order(value) else value
    body = _request_surface(text)
    m = _BODY_TAG.search(body) or _BODY_TAG_LOOSE.search(body)
    if m:
        return m.group(1).lower()
    return ORDER if _callout_order(body) else None


def order(text: str) -> str:
    """The user's own words for this job, or "".

    For an order this is the whole request. Next to a verb it refines the job —
    ``tiro: dispatch`` with ``> [!tiro] make it a Bug`` — and cannot widen it:
    the runner applies the verb's checks whatever the words say.

    From the ``tiro:`` value when it is a sentence, and from the last
    ``> [!tiro]`` callout in the user's prose (the newest, when the note is a
    conversation). Never from Tiro's blocks or from code.
    """
    parts = []
    value = (read_keys(text).get("tiro") or "").strip()
    if _is_order(value):
        parts.append(value)
    callout = _callout_order(_request_surface(text))
    if callout:
        parts.append(callout)
    return "\n\n".join(parts)


def _callout_order(body: str) -> str:
    found: list[list[str]] = []
    current: list[str] | None = None
    for line in body.split("\n"):
        m = _ORDER_CALLOUT.match(line)
        if m:
            current = [m.group(1).strip()]
            found.append(current)
            continue
        cont = _CALLOUT_LINE.match(line) if current is not None else None
        if cont:
            current.append(cont.group(1).rstrip())
        else:
            current = None
    for lines in reversed(found):
        words = "\n".join(lines).strip()
        if words:
            return words
    return ""


def verbs(text: str) -> list[str]:
    """Every request on the note, in the order they will run.

    A note may ask for several things: `#tiro/research` on one line and
    `#tiro/file` at the end. Reading only the first, as ``verb`` does, dropped
    the rest without a word. The frontmatter value comes first, then body tags
    in document order, each once. ``file`` always runs last, since it moves
    the note away from under anything after it. ``hold`` is not a job and is
    left out.

    A sentence in ``tiro:`` is :data:`ORDER`, as in ``verb``. A ``> [!tiro]``
    callout is one only on a note that asks for nothing else; next to a verb
    it refines that job instead.
    """
    found: list[str] = []
    keys = read_keys(text)
    value = (keys.get("tiro") or "").strip()
    if value:
        found.append(ORDER if _is_order(value) else value)
    body = _request_surface(text)
    tags = [(m.start(), m.group(1).lower()) for m in _BODY_TAG.finditer(body)]
    tags += [(m.start(), m.group(1).lower()) for m in _BODY_TAG_LOOSE.finditer(body)]
    for _, word in sorted(tags):
        if word not in found:
            found.append(word)
    if not found and _callout_order(body):
        found.append(ORDER)
    found = [v for v in found if v != HOLD]
    return sorted(found, key=lambda v: v == "file")


def done_verbs(text: str) -> set[str]:
    """The requests already answered at the note's recorded hash.

    Kept in ``tiro/done``. A note from before that key existed has one: the
    job named on each of its blocks.
    """
    keys = read_keys(text)
    if "tiro/done" in keys:
        return {v for v in re.split(r"[,\s]+", keys["tiro/done"]) if v}
    jobs = {b.job for b in find_blocks(text)}
    # No blocks at all: a job that answered with nothing. Then the hash alone
    # said "done", for whatever was asked.
    return jobs or set(verbs(text))


def pending(text: str) -> list[str]:
    """The requests on this note that still need a job: all of them if the
    user's content changed since Tiro last recorded it, otherwise the ones not
    yet answered at that content."""
    wanted = verbs(text)
    if not wanted:
        return []
    if unseen(text):
        return wanted
    done = done_verbs(text)
    return [v for v in wanted if v not in done]


def mark_done(text: str, verb: str) -> str:
    """Record that ``verb`` has been answered at the note's current content.

    Call before the new hash is written: if the content changed since the last
    one, earlier answers were to older content and are dropped.
    """
    done = set() if unseen(text) else done_verbs(text)
    done.add(verb)
    order = [v for v in verbs(text) if v in done] + sorted(done - set(verbs(text)))
    return set_key(text, "tiro/done", ", ".join(order))


def block_id(text: str, note_id: str, verb: str) -> str:
    """The id of this job's block on this note: one block per job.

    Blocks used to be keyed by the note's id alone, so every job on a note
    wrote into the same block and research replaced triage's proposal. A
    block from then keeps its id when its own job writes it again, so it is
    replaced in place rather than left beside a new one.
    """
    if any(b.id == note_id and b.job == verb for b in find_blocks(text)):
        return note_id
    return f"{note_id}-{verb}"


def _request_surface(text: str) -> str:
    """The part of a note a body tag may be read from: the user's prose, with
    Tiro's own blocks and any code removed. Tiro's output must never be able
    to queue a job, and neither should an example in a code span."""
    _, body, _ = split_frontmatter(text)
    return without_code(strip_blocks(body))


def looks_like_request(text: str) -> bool:
    """A ``#tiro`` tag in the body that ``verb`` did not recognise.

    Silently ignoring these is how a user concludes Tiro does not work. Lint
    names them instead.
    """
    if verb(text) is not None:
        return False
    return bool(_BODY_TAG_ANY.search(_request_surface(text)))


def note_id(text: str) -> str | None:
    return read_keys(text).get("tiro/id") or None


def ensure_id(text: str) -> tuple[str, str]:
    """Return (text, id), assigning a stable uuid on first touch."""
    existing = note_id(text)
    if existing:
        return text, existing
    new = uuid.uuid4().hex[:12]
    return set_key(text, "tiro/id", new), new


# --------------------------------------------------------------------------
# blocks
# --------------------------------------------------------------------------


def find_blocks(text: str) -> list[Block]:
    """Every well-formed Tiro block, in document order.

    A begin marker with no matching end is not a block: it is left alone and
    reported by lint. Silently repairing it could swallow user text that
    happens to sit after it.
    """
    lines = text.split("\n")
    blocks: list[Block] = []
    open_at: tuple[int, str, str] | None = None
    for i, line in enumerate(lines):
        b = _BLOCK_BEGIN.match(line)
        if b:
            open_at = (i, b.group(1), b.group(2))
            continue
        e = _BLOCK_END.match(line)
        if e and open_at and e.group(1) == open_at[2]:
            start, job, bid = open_at
            blocks.append(Block(job=job, id=bid, start=start, end=i,
                                body="\n".join(lines[start + 1 : i])))
            open_at = None
    return blocks


def upsert_block(text: str, *, job: str, id: str, body: str) -> str:
    """Replace the block with this id, or append one at the end of the note.

    Replacing in place is what makes a re-run idempotent: same id, same slot, no
    duplicate blocks and a diff that shows only what actually changed.
    """
    begin = f"<!-- tiro:begin job={job} id={id} -->"
    end = f"<!-- tiro:end id={id} -->"
    new = [begin, *body.rstrip("\n").split("\n"), end]
    lines = text.split("\n")
    for block in find_blocks(text):
        if block.id == id:
            lines[block.start : block.end + 1] = new
            return "\n".join(lines)
    tail = "\n".join(lines).rstrip("\n")
    return tail + "\n\n" + "\n".join(new) + "\n"


def remove_block(text: str, id: str) -> str:
    lines = text.split("\n")
    for block in find_blocks(text):
        if block.id == id:
            del lines[block.start : block.end + 1]
            return "\n".join(lines)
    return text


def strip_blocks(text: str) -> str:
    lines = text.split("\n")
    for block in sorted(find_blocks(text), key=lambda b: b.start, reverse=True):
        del lines[block.start : block.end + 1]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# the hash
# --------------------------------------------------------------------------


def user_content(text: str) -> str:
    """The note as the user wrote it: no Tiro state keys, no Tiro blocks. The
    verb and the accepted destination stay in, because the user edits them.

    Whitespace is normalised so that *adding or removing a Tiro block cannot
    change the result*. Without that, Tiro's own output would change the hash,
    the note would look edited on the next run, and the loop would never
    terminate. The cost is that the hash is blind to the user adding a blank
    line, which is not a request for work.
    """
    stripped = strip_blocks(strip_keys(text, for_hash=True))
    stripped = stripped.replace("\r\n", "\n").replace("\r", "\n")
    stripped = re.sub(r"\n{2,}", "\n\n", stripped)
    return stripped.strip()


def user_hash(text: str) -> str:
    return hashlib.sha256(user_content(text).encode("utf-8")).hexdigest()[:16]


def needs_work(text: str) -> bool:
    """The one rule (DESIGN section 4.2).

    Act when a verb is present and either we have never hashed this note, the
    user's content has changed since we did, or that verb has not yet been
    answered at this content (``pending``).
    """
    return bool(pending(text))


def unseen(text: str) -> bool:
    """Tiro has never hashed this note, or the user's content has changed since.

    The half of the one rule that auto mode shares: an untagged note in an auto
    folder is looked at on exactly this condition (ITERATION-3).
    """
    recorded = read_keys(text).get("tiro/hash")
    return not recorded or recorded != user_hash(text)
