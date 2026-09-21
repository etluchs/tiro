from __future__ import annotations

from pathlib import Path

from tiro.links import Index, iter_links


def test_parses_every_wikilink_shape() -> None:
    text = (
        "[[plain]] [[target|alias]] [[note#heading]] [[note#^block]] ![[embed]]\n"
        "[md link](Areas/other.md) [external](https://example.com)\n"
    )
    links = iter_links(text)
    targets = [(l.target, l.subpath, l.alias, l.embed) for l in links]
    assert ("plain", "", "", False) in targets
    assert ("target", "", "alias", False) in targets
    assert ("note", "#heading", "", False) in targets
    assert ("note", "#^block", "", False) in targets
    assert ("embed", "", "", True) in targets
    assert ("Areas/other.md", "", "md link", False) in targets
    assert not any("example.com" in t[0] for t in targets)


def test_links_inside_code_are_not_links() -> None:
    text = "```\n[[not a link]]\n```\n\nSome `[[also not]]` text and [[real]].\n"
    assert [l.target for l in iter_links(text)] == ["real"]


def test_resolves_by_path_basename_and_alias(vault: Path) -> None:
    idx = Index(vault)
    assert idx.resolve("Areas/Compute trends") == "Areas/Compute trends.md"
    assert idx.resolve("Compute trends") == "Areas/Compute trends.md"
    assert idx.resolve("Scaling") == "Areas/Compute trends.md"  # via aliases


def test_unresolved_finds_the_broken_link_and_only_that(vault: Path) -> None:
    broken = {(rel, link.target) for rel, link in Index(vault).unresolved()}
    assert ("Teaching/HS26/prog-2.md", "missing-note") in broken
    assert not any(t == "Compute trends" for _, t in broken)


def test_subpath_links_resolve_to_the_note(vault: Path) -> None:
    assert Index(vault).resolve("Compute trends") == "Areas/Compute trends.md"
    broken = {t for _, t in Index(vault).unresolved()}
    assert "Compute trends" not in broken  # the #Scaling link is not broken


def test_backlinks(vault: Path) -> None:
    assert "Teaching/HS26/prog-2.md" in Index(vault).backlinks("Areas/Compute trends.md")


def test_tiro_state_is_not_part_of_the_vault_graph(vault: Path) -> None:
    assert not any(p.startswith(".tiro") for p in Index(vault).paths)
