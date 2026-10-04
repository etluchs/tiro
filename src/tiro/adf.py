"""Markdown to Atlassian Document Format, for a Jira description.

Jira Cloud's create API takes a description only as ADF, a JSON tree, and shows
anything else as the literal text it was. The first live dispatch (2026-10-04)
sent plain paragraphs, and the spec arrived with its `##` and `**` and `1.`
showing. The spec is written in markdown, the user signed it off in markdown,
and this turns that markdown into the tree Jira renders.

Deliberately small, and conservative where it is unsure. It covers what a spec
uses: headings, paragraphs, bullet and numbered lists (nested by indentation),
fenced code, quotes, rules, and inline bold, italic, strike, code and links.
Anything it does not understand stays text, never a guess at structure: Jira
rejects an invalid tree outright, and a create that fails is a create the user
has to look at.

Three things ADF demands, and this keeps:

- no empty text nodes, anywhere;
- the ``code`` mark combines with nothing but ``link``;
- a link is a mark with an ``href``, so only real URLs become links. A vault
  ``[[wikilink]]`` means nothing in Jira and becomes its text.
"""

from __future__ import annotations

import re

_FENCE = re.compile(r"^\s*(```|~~~)\s*([\w+-]*)\s*$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_QUOTE = re.compile(r"^\s*>\s?(.*)$")
_ITEM = re.compile(r"^(\s*)([-*+]|(\d{1,9})[.)])\s+(.*)$")

_INLINE = re.compile(
    r"(?P<code>`+)(?P<code_body>.+?)(?P=code)"
    r"|\[\[(?P<wiki>[^\]|]+)(?:\|(?P<wiki_alias>[^\]]+))?\]\]"
    r"|\[(?P<link_text>[^\]]+)\]\((?P<link_href>[^)\s]+)(?:\s+\"[^\"]*\")?\)"
    r"|<(?P<auto>https?://[^>\s]+)>"
    r"|(?P<bare>https?://[^\s<>()]+[^\s<>().,;:!?'\"])"
    r"|\*\*(?P<strong>.+?)\*\*"
    r"|(?<!\w)__(?P<strong_u>.+?)__(?!\w)"
    r"|~~(?P<strike>.+?)~~"
    r"|\*(?P<em>[^*\s](?:.*?[^*\s])?)\*"
    r"|(?<!\w)_(?P<em_u>[^_\s](?:.*?[^_\s])?)_(?!\w)"
)
_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!~>|])")
#: An escaped character is moved out of the way before inline parsing, into
#: the private-use plane, so that `\*` can never open or close emphasis, and
#: put back as itself when the text node is made.
_PRIVATE = 0xE000
_LINKABLE = re.compile(r"^(https?://|mailto:)", re.I)


def from_markdown(text: str) -> dict:
    """The whole description as an ADF doc."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    content = _blocks(lines)
    return {"type": "doc", "version": 1, "content": content or [_para("(no description)")]}


# --------------------------------------------------------------------------
# blocks
# --------------------------------------------------------------------------


def _blocks(lines: list[str]) -> list[dict]:
    out: list[dict] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue

        fence = _FENCE.match(line)
        if fence:
            body: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(fence.group(1)):
                body.append(lines[i])
                i += 1
            i += 1  # the closing fence, or the end
            node: dict = {"type": "codeBlock"}
            if fence.group(2):
                node["attrs"] = {"language": fence.group(2)}
            code = "\n".join(body)
            node["content"] = [{"type": "text", "text": code}] if code else []
            out.append(node)
            continue

        heading = _HEADING.match(line)
        if heading:
            inline = _inline(heading.group(2))
            if inline:
                out.append({"type": "heading", "attrs": {"level": len(heading.group(1))},
                            "content": inline})
            i += 1
            continue

        if _RULE.match(line):
            out.append({"type": "rule"})
            i += 1
            continue

        if _QUOTE.match(line):
            quoted: list[str] = []
            while i < len(lines) and _QUOTE.match(lines[i]):
                quoted.append(_QUOTE.match(lines[i]).group(1))
                i += 1
            inner = [b for b in _blocks(quoted) if b["type"] in _QUOTABLE]
            if inner:
                out.append({"type": "blockquote", "content": inner})
            continue

        if _ITEM.match(line):
            block: list[str] = []
            while i < len(lines):
                cur = lines[i]
                if _ITEM.match(cur) or (block and cur.strip() and cur[:1] in " \t"):
                    block.append(cur)
                    i += 1
                elif not cur.strip() and i + 1 < len(lines) and (
                        _ITEM.match(lines[i + 1]) or lines[i + 1][:1] in " \t") \
                        and lines[i + 1].strip():
                    i += 1  # a loose list: a blank line between items
                else:
                    break
            out.extend(_lists(block))
            continue

        para: list[str] = []
        while i < len(lines) and lines[i].strip() and not _starts_block(lines[i]):
            para.append(lines[i].strip())
            i += 1
        node = _paragraph(para)
        if node:
            out.append(node)
    return out


_QUOTABLE = {"paragraph", "bulletList", "orderedList", "codeBlock", "heading"}


def _starts_block(line: str) -> bool:
    return bool(_FENCE.match(line) or _HEADING.match(line) or _RULE.match(line)
                or _QUOTE.match(line) or _ITEM.match(line))


def _paragraph(lines: list[str]) -> dict | None:
    """Lines inside one paragraph keep their breaks, as Obsidian shows them."""
    content: list[dict] = []
    for line in lines:
        inline = _inline(line)
        if not inline:
            continue
        if content:
            content.append({"type": "hardBreak"})
        content.extend(inline)
    return {"type": "paragraph", "content": content} if content else None


def _para(text: str) -> dict:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def _lists(lines: list[str]) -> list[dict]:
    """Consecutive list lines as nested lists. An item's children are the
    lines indented past its own marker; a change of marker kind at the same
    depth starts a new list, as it does in markdown."""
    items: list[tuple[int, bool, int | None, list[str]]] = []  # indent, ordered, start, text
    for line in lines:
        m = _ITEM.match(line)
        if m:
            indent = len(m.group(1).expandtabs(4))
            number = int(m.group(3)) if m.group(3) else None
            items.append((indent, number is not None, number, [m.group(4)]))
        elif items:
            items[-1][3].append(line.strip())
    out, _ = _build(items, 0, items[0][0] if items else 0)
    return out


def _build(items, i: int, depth: int) -> tuple[list[dict], int]:
    lists: list[dict] = []
    current: dict | None = None
    while i < len(items):
        indent, ordered, start, text = items[i]
        if indent < depth:
            break
        if indent > depth:
            # Deeper than this level: children of the previous item.
            children, i = _build(items, i, indent)
            if current and current["content"]:
                current["content"][-1]["content"].extend(children)
            else:
                lists.extend(children)
            continue
        kind = "orderedList" if ordered else "bulletList"
        if current is None or current["type"] != kind:
            current = {"type": kind, "content": []}
            if ordered and start not in (None, 1):
                current["attrs"] = {"order": start}
            lists.append(current)
        body = _paragraph(text) or _para(" ")  # a list item may not be empty
        current["content"].append({"type": "listItem", "content": [body]})
        i += 1
    return lists, i


# --------------------------------------------------------------------------
# inline
# --------------------------------------------------------------------------


def _inline(text: str, marks: tuple = ()) -> list[dict]:
    if not marks:
        text = _ESCAPE.sub(lambda m: chr(_PRIVATE + ord(m.group(1))), text)
    out: list[dict] = []
    pos = 0
    for m in _INLINE.finditer(text):
        out += _text(text[pos:m.start()], marks)
        pos = m.end()
        g = m.groupdict()
        if g["code"]:
            # `code` combines with nothing but a link.
            keep = tuple(mk for mk in marks if mk[0] == "link")
            out += _text(g["code_body"].strip() or g["code_body"], keep + (("code",),))
        elif g["wiki"]:
            out += _text((g["wiki_alias"] or g["wiki"].split("#")[0]).strip(), marks)
        elif g["link_text"] is not None:
            href = g["link_href"]
            if _LINKABLE.match(href) and not _has_link(marks):
                out += _inline(g["link_text"], marks + (("link", href),))
            else:
                out += _inline(g["link_text"], marks)
        elif g["auto"] or g["bare"]:
            url = g["auto"] or g["bare"]
            out += _text(url, marks if _has_link(marks) else marks + (("link", url),))
        elif g["strong"] or g["strong_u"]:
            out += _inline(g["strong"] or g["strong_u"], _add(marks, ("strong",)))
        elif g["strike"]:
            out += _inline(g["strike"], _add(marks, ("strike",)))
        elif g["em"] or g["em_u"]:
            out += _inline(g["em"] or g["em_u"], _add(marks, ("em",)))
    out += _text(text[pos:], marks)
    return _merge(out)


def _has_link(marks: tuple) -> bool:
    return any(mk[0] == "link" for mk in marks)


def _add(marks: tuple, mark: tuple) -> tuple:
    return marks if mark in marks else marks + (mark,)


def _restore(text: str) -> str:
    return "".join(chr(ord(c) - _PRIVATE) if _PRIVATE <= ord(c) < _PRIVATE + 128 else c
                   for c in text)


def _text(text: str, marks: tuple) -> list[dict]:
    text = _restore(text)
    if not text:
        return []
    node: dict = {"type": "text", "text": text}
    if marks:
        node["marks"] = [
            {"type": "link", "attrs": {"href": mk[1]}} if mk[0] == "link" else {"type": mk[0]}
            for mk in marks
        ]
    return [node]


def _merge(nodes: list[dict]) -> list[dict]:
    out: list[dict] = []
    for node in nodes:
        if out and node.get("type") == "text" and out[-1].get("type") == "text" \
                and out[-1].get("marks") == node.get("marks"):
            out[-1] = {**out[-1], "text": out[-1]["text"] + node["text"]}
        else:
            out.append(node)
    return out
