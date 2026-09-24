"""``connect``: links and contradictions, each checked before it is written.

The feature was held back as the one most likely to produce plausible nonsense
at volume. These tests are the reason it ships anyway: a suggestion reaches the
note only if the target is a real note Tiro may quote, and both quotations are
really there in the user's own words.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tiro import connect, lint, protocol, runner
from tiro.agent import AgentError, JobOutput, ScriptedAgent
from tiro.ops_fs import FilesystemOps

NOTE = "00 Inbox/scaling.md"
TEXT = """---
tiro: connect
---

# Scaling

General methods that leverage computation are ultimately the most effective.
Hand-built features still win when the data set is tiny, in my experience.
"""
HERE = "General methods that leverage computation are ultimately the most effective"
THERE = "Links to [[The Bitter Lesson|the bitter lesson]] and [[Teaching/HS26/prog-2]]"


def _link(target="Areas/Compute trends", here=HERE, there=THERE, why="measures the trend"):
    return {"target": target, "here": here, "there": there, "why": why}


@pytest.fixture
def note(vault: Path) -> str:
    (vault / NOTE).write_text(TEXT, encoding="utf-8")
    return TEXT


def _check(config, payload, text=TEXT):
    return connect.check(config, NOTE, text, payload)


def _run(config, payload, **extra):
    for path in config.vault.rglob("*.md"):
        os.utime(path, (0, 0))
    results = {"triage": JobOutput(block="y"), "research": JobOutput(block="x"),
               "connect": JobOutput(payload=payload, **extra)}
    record = runner.once(config, agent=ScriptedAgent(results), ops=FilesystemOps(config.vault))
    return record, (config.vault / NOTE).read_text()


def test_a_checked_link_reaches_the_note_and_the_prose_is_untouched(config, vault, note) -> None:
    record, text = _run(config, {"links": [_link()]})

    entry = next(e for e in record.entries if e.note == NOTE)
    assert (entry.outcome, entry.verb) == ("done", "connect")
    assert "1 links, 0 contradictions proposed; 0 dropped" in entry.detail
    assert "- [[Areas/Compute trends|Compute trends]] — measures the trend" in text
    assert f"here: “{HERE}”" in text
    # Quoted as text: a link copied from another note need not resolve here.
    assert "there: “Links to the bitter lesson and Teaching/HS26/prog-2”" in text
    assert protocol.user_content(text) == protocol.user_content(TEXT)


def test_a_quotation_that_is_not_in_this_note_drops_the_suggestion(config, note) -> None:
    out = _check(config, {"links": [_link(here="Scaling laws are the only thing that matters here")]})
    assert out.links == [] and "not in it" in out.dropped[0]


def test_a_quotation_that_is_not_in_the_target_drops_the_suggestion(config, note) -> None:
    out = _check(config, {"links": [_link(there="Compute trends predict every benchmark result")]})
    assert out.links == [] and "`Areas/Compute trends.md` is not in it" in out.dropped[0]


def test_a_quotation_from_tiros_own_block_is_not_evidence(config, vault, note) -> None:
    target = vault / "Areas/orphan.md"
    target.write_text(protocol.upsert_block(
        target.read_text(), job="research", id="x",
        body="> Tiro wrote this sentence, and the user never did."), encoding="utf-8")
    out = _check(config, {"links": [_link("Areas/orphan", there="Tiro wrote this sentence, and the user never did")]})
    assert out.links == []


def test_whitespace_case_and_curly_quotes_do_not_matter(config, note) -> None:
    out = _check(config, {"links": [_link(here="general  methods that leverage\ncomputation ARE ultimately",
                                          there="Links to [[The Bitter Lesson|the bitter lesson]]")]})
    assert len(out.links) == 1


@pytest.mark.parametrize("target, why", [
    ("Areas/Nowhere", "is not a note in this vault"),
    ("00 Inbox/scaling", "to itself"),
    ("Compute trends", "already linked"),
])
def test_only_real_new_targets(config, vault, note, target, why) -> None:
    text = TEXT + "\nSee [[Compute trends]].\n" if why == "already linked" else TEXT
    out = _check(config, {"links": [_link(target)]}, text=text)
    assert out.links == [] and why in out.dropped[0]


def test_a_private_note_is_never_quoted_into_another(config, vault, note) -> None:
    (vault / "Private").mkdir()
    (vault / "Private/diary.md").write_text(
        "General methods that leverage computation are what I dream about.\n", encoding="utf-8")
    out = _check(config, {"links": [_link("Private/diary", there="General methods that leverage computation")]})
    assert out.links == [] and "private" in out.dropped[0]


def test_short_quotations_prove_nothing(config, note) -> None:
    out = _check(config, {"links": [_link(here="the most effective")]})
    assert out.links == [] and "too short" in out.dropped[0]


def test_no_more_than_five(config, vault, note) -> None:
    for i in range(7):
        (vault / f"Areas/n{i}.md").write_text(f"Note {i} agrees that computation beats cleverness.\n",
                                              encoding="utf-8")
    out = _check(config, {"links": [
        _link(f"Areas/n{i}", there=f"Note {i} agrees that computation beats cleverness")
        for i in range(7)]})
    assert [x["rel"] for x in out.links] == [f"Areas/n{i}.md" for i in range(5)]
    assert out.dropped == ["more than 5 links"] * 2


def test_contradictions_are_named_with_both_sides(config, vault, note) -> None:
    (vault / "Areas/small data.md").write_text(
        "On small data sets, learned features beat hand-built ones every time.\n", encoding="utf-8")
    _, text = _run(config, {"contradictions": [{
        "target": "Areas/small data", "why": "disagrees about small data",
        "here": "Hand-built features still win when the data set is tiny",
        "there": "learned features beat hand-built ones every time"}]})
    assert "**Contradictions**\n- [[Areas/small data|small data]] — disagrees about small data" in text


def test_nothing_found_is_said_plainly(config, note) -> None:
    _, text = _run(config, {"links": [], "contradictions": []})
    assert "Nothing in the vault that this note should link to" in text


def test_dropped_suggestions_are_counted_on_the_note(config, note) -> None:
    _, text = _run(config, {"links": [_link(), _link("Areas/Nowhere")]})
    assert "*1 further suggestion dropped because it could not be checked.*" in text


def test_the_agents_own_block_and_keys_never_reach_the_note(config, note) -> None:
    _, text = _run(config, {"links": []}, block="> [!tip] [[Injected]] trust me",
                   keys={"tiro/filed-to": "Archive/scaling.md"})
    assert "Injected" not in text
    assert "tiro/filed-to" not in protocol.read_keys(text)


def test_no_payload_blocks_the_note_and_waits_for_the_user(config, note) -> None:
    record, text = _run(config, None)
    entry = next(e for e in record.entries if e.note == NOTE)
    assert entry.outcome == "blocked" and "retries by itself" not in entry.detail
    assert protocol.read_keys(text).get("tiro/hash")
    with pytest.raises(AgentError):
        _check(config, {"links": "not a list"})


def test_a_proposed_link_does_not_unorphan_its_target(config, vault, note) -> None:
    (vault / "Areas/orphan.md").write_text(
        "Nothing links here, and it thinks computation is overrated for the most part.\n",
        encoding="utf-8")
    _run(config, {"links": [_link("Areas/orphan",
                                  there="it thinks computation is overrated for the most part")]})
    assert "[[Areas/orphan|orphan]]" in (vault / NOTE).read_text()
    assert "Areas/orphan.md" in lint._orphans(vault)


def test_connect_is_a_verb(vault) -> None:
    assert "connect" in protocol.VERBS
    assert protocol.verb("Some text.\n\n#tiro connect\n") == "connect"
