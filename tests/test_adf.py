"""Markdown to ADF, for the Jira description.

Every test runs the result through :func:`_valid`, a checker for the parts of
the ADF schema Jira enforces on create: which nodes may hold which, no empty
text, marks only where allowed. An invalid tree is a failed create on the live
site, so the checker is the point of the file as much as the shapes are.
"""

from __future__ import annotations

from tiro.adf import from_markdown
from tiro.jira import build_payload, to_adf

INLINE = {"text", "hardBreak"}
CHILDREN = {
    "doc": {"paragraph", "heading", "bulletList", "orderedList", "codeBlock",
            "blockquote", "rule"},
    "paragraph": INLINE,
    "heading": INLINE,
    "bulletList": {"listItem"},
    "orderedList": {"listItem"},
    "listItem": {"paragraph", "bulletList", "orderedList", "codeBlock"},
    "blockquote": {"paragraph", "bulletList", "orderedList", "codeBlock", "heading"},
    "codeBlock": {"text"},
}
MARKS = {"strong", "em", "strike", "code", "link"}


def _valid(node: dict) -> None:
    kind = node["type"]
    if kind == "text":
        assert node["text"], "ADF refuses empty text"
        marks = [m["type"] for m in node.get("marks", [])]
        assert set(marks) <= MARKS and len(marks) == len(set(marks))
        if "code" in marks:
            assert set(marks) <= {"code", "link"}, "code combines only with link"
        for m in node.get("marks", []):
            if m["type"] == "link":
                assert m["attrs"]["href"].startswith(("http://", "https://", "mailto:"))
        return
    if kind in ("hardBreak", "rule"):
        assert "content" not in node
        return
    allowed = CHILDREN[kind]
    content = node.get("content", [])
    if kind not in ("codeBlock",):
        assert content, f"{kind} may not be empty"
    if kind == "listItem":
        assert content[0]["type"] == "paragraph"
    if kind == "heading":
        assert 1 <= node["attrs"]["level"] <= 6
    if kind == "codeBlock":
        assert all("marks" not in c for c in content)
    for child in content:
        assert child["type"] in allowed, f"{child['type']} inside {kind}"
        _valid(child)


def _doc(md: str) -> list[dict]:
    doc = from_markdown(md)
    assert doc["type"] == "doc" and doc["version"] == 1
    _valid(doc)
    return doc["content"]


def _texts(node: dict) -> str:
    if node["type"] == "text":
        return node["text"]
    return "".join(_texts(c) for c in node.get("content", []))


SPEC = """## Problem

The ingest endpoint has **no rate limit**, so one client can starve the rest.

## Acceptance criteria

1. Requests over `100/min` per key get a `429`.
2. The limit is configurable:
   - per key
   - globally
3. See [the RFC](https://www.rfc-editor.org/rfc/rfc6585) and [[Rate limiting]].

## Non-goals

- Billing. *Not* this issue.

```python
limit = 100
```

> Quoted from the user: keep it simple.

---
Filed by Tiro from `00 Inbox/a.md` (run r1).
"""


def test_a_spec_becomes_structure_not_literal_markup() -> None:
    content = _doc(SPEC)
    kinds = [n["type"] for n in content]
    assert kinds == ["heading", "paragraph", "heading", "orderedList", "heading",
                     "bulletList", "codeBlock", "blockquote", "rule", "paragraph"]
    assert content[0] == {"type": "heading", "attrs": {"level": 2},
                          "content": [{"type": "text", "text": "Problem"}]}
    flat = "".join(_texts(n) for n in content)
    for literal in ("##", "**", "1. ", "```", "[[", "]("):
        assert literal not in flat


def test_inline_marks() -> None:
    [para] = _doc("A **bold** and *em* and ~~gone~~ and `x = 1`.")
    marks = {n["text"]: [m["type"] for m in n.get("marks", [])] for n in para["content"]}
    assert marks == {"A ": [], "bold": ["strong"], " and ": [], "em": ["em"],
                     "gone": ["strike"], "x = 1": ["code"], ".": []}


def test_code_inside_bold_drops_bold_because_adf_requires_it() -> None:
    [para] = _doc("**run `make` now**")
    code = next(n for n in para["content"] if n["text"] == "make")
    assert [m["type"] for m in code["marks"]] == ["code"]


def test_links_only_for_real_urls() -> None:
    [para] = _doc("[RFC](https://x.org/a) [local](notes/a.md) <https://y.org> https://z.org/b.")
    links = [(n["text"], n["marks"][0]["attrs"]["href"]) for n in para["content"]
             if n.get("marks")]
    assert links == [("RFC", "https://x.org/a"), ("https://y.org", "https://y.org"),
                     ("https://z.org/b", "https://z.org/b")]
    assert "local" in _texts(para)  # kept as text, not a broken link


def test_wikilinks_become_their_text() -> None:
    [para] = _doc("See [[Areas/Compute trends|compute]] and [[Rate limiting#Why]].")
    assert _texts(para) == "See compute and Rate limiting."


def test_nested_lists_and_a_change_of_kind() -> None:
    content = _doc("- a\n  - a1\n  - a2\n- b\n1. one\n2. two\n")
    assert [n["type"] for n in content] == ["bulletList", "orderedList"]
    first = content[0]["content"][0]["content"]
    assert [n["type"] for n in first] == ["paragraph", "bulletList"]
    assert [_texts(i) for i in first[1]["content"]] == ["a1", "a2"]


def test_an_ordered_list_keeps_its_start() -> None:
    [ol] = _doc("3. three\n4. four\n")
    assert ol["attrs"] == {"order": 3}


def test_a_loose_list_stays_one_list() -> None:
    [ol] = _doc("1. one\n\n2. two\n\n3. three\n")
    assert len(ol["content"]) == 3


def test_lines_in_a_paragraph_keep_their_breaks() -> None:
    [para] = _doc("first line\nsecond line\n")
    assert [n["type"] for n in para["content"]] == ["text", "hardBreak", "text"]


def test_snake_case_and_stray_stars_stay_text() -> None:
    [para] = _doc("set max_jobs_per_run to 2 * 3 and a_b_c")
    assert _texts(para) == "set max_jobs_per_run to 2 * 3 and a_b_c"
    assert not any(n.get("marks") for n in para["content"])


def test_escapes_are_honoured() -> None:
    [para] = _doc(r"not \*emphasis\* here")
    assert _texts(para) == "not *emphasis* here"


def test_nothing_empty_reaches_jira() -> None:
    for md in ("", "\n\n\n", "#  \n", "- \n", "> \n", "```\n```\n", "**​**"):
        _doc(md)


def test_a_table_stays_readable_text() -> None:
    [para] = _doc("| a | b |\n|---|---|\n| 1 | 2 |\n")
    assert _texts(para).startswith("| a | b |")


def test_the_payload_description_is_converted_and_signed() -> None:
    payload = build_payload({"summary": "Add rate limiting", "description": SPEC},
                            project="TIRO", default_type="Task", note_rel="00 Inbox/a.md",
                            note_id="abc", run_id="r1")
    body = payload.to_json("TIRO")
    _valid(body["description"])
    assert body["description"]["content"][0]["type"] == "heading"
    footer = body["description"]["content"][-1]
    assert "Filed by Tiro from 00 Inbox/a.md" in _texts(footer)
    # The preview the user reviews is the markdown itself.
    assert payload.to_json("TIRO", adf=False)["description"].startswith("## Problem")
    assert to_adf(SPEC) == from_markdown(SPEC)
