"""``connect``: propose links, and name contradictions, between notes.

The feature most likely to produce plausible nonsense at volume (ITERATION-2,
"what this plan deliberately leaves alone"). It ships on the condition that
nonsense cannot reach the note unchecked, and the checks are the runner's, not
the model's:

- **Asked for, per note.** ``tiro: connect`` on one note. There is no sweep.
- **Every suggestion carries evidence.** A verbatim passage from this note and
  one from the target. The runner looks for both, in the user's own text
  (outside Tiro's blocks). A quotation that is not there is a suggestion that
  is not made, and the block says how many were dropped.
- **Only real notes.** The target must resolve to a note in the vault, not
  this one, not already linked from it, and not in an L0 folder — a private
  note is never quoted into another.
- **Few.** At most :data:`MAX_LINKS` links and :data:`MAX_CONTRADICTIONS`
  contradictions, in the order the agent ranked them.
- **Proposals, not edits.** The links go in Tiro's block. The user copies the
  ones they want into their prose; the rest go when the block is stripped.

The runner renders the block itself from what survived. The agent's own
``block``, if any, is not used: the only text of the agent's that reaches the
note is a one-line reason per suggestion and the quotations, both checked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from tiro import protocol
from tiro.agent import AgentError, JobOutput
from tiro.config import Config
from tiro.links import Index, iter_links

MAX_LINKS = 5
MAX_CONTRADICTIONS = 3
#: A quotation shorter than this proves nothing: "the model" is in every note.
MIN_QUOTE = 20
MAX_QUOTE = 300
MAX_WHY = 160


@dataclass
class Checked:
    links: list[dict] = field(default_factory=list)
    contradictions: list[dict] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)  # one reason per dropped suggestion


_WIKI = re.compile(r"!?\[\[([^\]|]*)(?:\|([^\]]*))?\]\]")
_MD = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")


def _plain(text: str) -> str:
    """Links reduced to what they display. A quotation is text, and a link
    copied into Tiro's block from another note may not resolve from this one:
    the gate would call it a broken link, rightly."""
    text = _WIKI.sub(lambda m: m.group(2) or m.group(1).split("#")[0], text)
    return _MD.sub(r"\1", text)


def _norm(text: str) -> str:
    """For finding a quotation: case, whitespace, curly quotes, links and
    markdown emphasis are not what makes a passage the same passage."""
    text = _plain(text)
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = re.sub(r"[*_`]", "", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _prose(text: str) -> str:
    """What the user wrote: no frontmatter, no Tiro blocks."""
    _, body, _ = protocol.split_frontmatter(text)
    return _norm(protocol.strip_blocks(body))


def _quote(item: dict, key: str) -> str:
    return re.sub(r"\s+", " ", _plain(str(item.get(key) or ""))).strip().strip('"“”').strip()


def check(config: Config, note_rel: str, note_text: str, payload: dict | None) -> Checked:
    """Keep what can be verified. Everything else is dropped, and counted."""
    if not isinstance(payload, dict):
        raise AgentError("connect needs a `payload` with `links` and `contradictions`")
    index = Index(config.vault)
    here = _prose(note_text)
    already = {index.resolve(link.target, from_rel=note_rel)
               for link in iter_links(protocol.strip_blocks(note_text))}
    out = Checked()
    seen: set[tuple[str, str]] = set()

    for kind, cap in (("links", MAX_LINKS), ("contradictions", MAX_CONTRADICTIONS)):
        items = payload.get(kind) or []
        if not isinstance(items, list):
            raise AgentError(f"connect: `{kind}` must be a list")
        kept = out.links if kind == "links" else out.contradictions
        for item in items:
            if not isinstance(item, dict):
                out.dropped.append("not a suggestion")
                continue
            target = str(item.get("target") or "").strip().strip("[]")
            ours, theirs = _quote(item, "here"), _quote(item, "there")
            why = re.sub(r"\s+", " ", _plain(str(item.get("why") or ""))).strip()
            rel = index.resolve(target.split("|")[0], from_rel=note_rel) if target else None

            if rel is None or not rel.endswith(".md"):
                out.dropped.append(f"`{target}` is not a note in this vault")
            elif rel == note_rel:
                out.dropped.append("a link from the note to itself")
            elif config.trust.level_for(rel) == "L0":
                out.dropped.append("the target is in a private folder")
            elif kind == "links" and rel in already:
                out.dropped.append(f"`{rel}` is already linked")
            elif (kind, rel) in seen:
                out.dropped.append(f"`{rel}` twice")
            elif not why:
                out.dropped.append(f"no reason given for `{rel}`")
            elif not (MIN_QUOTE <= len(ours) <= MAX_QUOTE and MIN_QUOTE <= len(theirs) <= MAX_QUOTE):
                out.dropped.append(f"a quotation too short or too long to check, for `{rel}`")
            elif _norm(ours) not in here:
                out.dropped.append(f"the passage quoted from this note is not in it, for `{rel}`")
            elif _norm(theirs) not in _prose(
                    (config.vault / rel).read_text(encoding="utf-8", errors="replace")):
                out.dropped.append(f"the passage quoted from `{rel}` is not in it")
            elif len(kept) >= cap:
                out.dropped.append(f"more than {cap} {kind}")
            else:
                seen.add((kind, rel))
                kept.append({"rel": rel, "why": why[:MAX_WHY], "here": ours, "there": theirs})
    return out


def _link(rel: str) -> str:
    return f"[[{rel[:-3]}|{rel.rsplit('/', 1)[-1][:-3]}]]"


def _q(text: str) -> str:
    return text.replace("\n", " ")


def render(checked: Checked, *, today: str) -> str:
    n, c = len(checked.links), len(checked.contradictions)
    if n or c:
        found = " and ".join(part for part in (
            f"{n} note{'s' if n != 1 else ''} this one should know about" if n else "",
            f"{c} contradiction{'s' if c != 1 else ''}" if c else "") if part)
        head = f"> Found {found}. Copy the links you want into your text; the block is only a proposal."
    else:
        head = "> Nothing in the vault that this note should link to and does not already."
    lines = [f"> [!tip] Tiro · connect · {today}", head]
    for item in checked.links:
        lines += ["", f"- {_link(item['rel'])} — {item['why']}",
                  f"  - here: “{_q(item['here'])}”",
                  f"  - there: “{_q(item['there'])}”"]
    if checked.contradictions:
        lines += ["", "**Contradictions**"]
        for item in checked.contradictions:
            lines += [f"- {_link(item['rel'])} — {item['why']}",
                      f"  - here: “{_q(item['here'])}”",
                      f"  - there: “{_q(item['there'])}”"]
    if checked.dropped:
        k = len(checked.dropped)
        lines += ["", f"*{k} further suggestion{'s' if k != 1 else ''} dropped because "
                      f"{'they' if k != 1 else 'it'} could not be checked.*"]
    return "\n".join(lines)


def apply(config: Config, note_rel: str, note_text: str, output: JobOutput, *,
          today: str) -> JobOutput:
    """Replace the agent's answer with what survives checking."""
    if output.status != "done":
        return output  # a question, or a block with its reason: passed through
    checked = check(config, note_rel, note_text, output.payload)
    output.block = render(checked, today=today)
    output.keys = {}
    output.detail = (f"{len(checked.links)} links, {len(checked.contradictions)} "
                     f"contradictions proposed; {len(checked.dropped)} dropped")
    return output
