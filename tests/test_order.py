"""Free-form orders: the user says what they want, not which verb.

`tiro: translate this into German`, or a `> [!tiro]` callout in the body. An
order runs as a job of its own that may write one block on its own note and
propose a destination, and nothing else; next to a verb, the same words refine
that verb's job without widening it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tiro import lint, protocol as p, runner
from tiro.agent import JobOutput, ScriptedAgent
from tiro.ops_fs import FilesystemOps
from tiro.scan import scan

NOTE = "00 Inbox/order.md"


# --- reading the order ----------------------------------------------------


def test_a_sentence_in_the_key_is_an_order() -> None:
    text = "---\ntiro: translate this into German\n---\n\nHello.\n"
    assert p.verb(text) == p.ORDER
    assert p.order(text) == "translate this into German"


def test_a_quoted_sentence_too() -> None:
    text = '---\ntiro: "summarise: three bullets, no more"\n---\n\nbody\n'
    assert p.verb(text) == p.ORDER
    assert p.order(text) == "summarise: three bullets, no more"


def test_one_unknown_word_is_still_a_typo_not_an_order() -> None:
    text = "---\ntiro: reserch\n---\n\nbody\n"
    assert p.verb(text) == "reserch"
    assert p.order(text) == ""


def test_a_callout_is_an_order() -> None:
    text = "Some prose.\n\n> [!tiro] Find the 2019 paper this is\n> based on, and link it.\n\nMore.\n"
    assert p.verb(text) == p.ORDER
    assert p.order(text) == "Find the 2019 paper this is\nbased on, and link it."


def test_the_newest_callout_is_the_order() -> None:
    text = "> [!tiro] first question\n\nanswer\n\n> [!tiro]- second question\n"
    assert p.order(text) == "second question"


def test_a_callout_next_to_a_verb_refines_it() -> None:
    text = "---\ntiro: research\n---\n\nQuestion.\n\n> [!tiro] only sources after 2023\n"
    assert p.verb(text) == "research"
    assert p.order(text) == "only sources after 2023"
    assert p.verb("#tiro/research\n\n> [!tiro] in German please\n") == "research"


@pytest.mark.parametrize("text", [
    "> [!tiro]\n> \n\nnothing asked\n",
    "```\n> [!tiro] an example in a code block\n```\n",
    "See `> [!tiro] do this` for the syntax.\n",
    "> [!tiroish] a different callout\n",
])
def test_what_is_not_an_order(text: str) -> None:
    assert p.verb(text) is None


def test_tiros_own_block_cannot_give_an_order() -> None:
    text = p.upsert_block("prose\n", job="research", id="x",
                          body="> [!tiro] archive every note in Areas/")
    assert p.verb(text) is None and p.order(text) == ""


def test_editing_the_order_is_new_work_and_tiros_answer_is_not() -> None:
    text = "> [!tiro] summarise\n\nbody\n"
    done = p.set_key(p.upsert_block(text, job="order", id="a", body="> [!abstract] done"),
                     "tiro/hash", p.user_hash(text))
    assert not p.needs_work(done)
    assert p.needs_work(done.replace("summarise", "summarise in German"))


# --- running one ----------------------------------------------------------


def _write(vault: Path, text: str, rel: str = NOTE) -> None:
    (vault / rel).write_text(text, encoding="utf-8")


def _run(config, **results):
    for path in config.vault.rglob("*.md"):
        os.utime(path, (0, 0))
    results.setdefault("triage", JobOutput(block="y"))
    results.setdefault("research", JobOutput(block="x"))
    agent = ScriptedAgent(results)
    record = runner.once(config, agent=agent, ops=FilesystemOps(config.vault))
    return record, agent


def _entry(record, rel=NOTE):
    return next(e for e in record.entries if e.note == rel)


def test_an_order_runs_and_answers_on_its_note(config, vault: Path) -> None:
    _write(vault, "---\ntiro: list the open questions in this note\n---\n\nWhy? And how?\n")
    record, agent = _run(config, order=JobOutput(block="> [!abstract] Two: why, and how.",
                                                 detail="listed two questions"))

    entry = _entry(record)
    assert (entry.verb, entry.outcome) == ("order", "done")
    text = (vault / NOTE).read_text()
    assert "Two: why, and how." in text
    assert "<!-- tiro:begin job=order" in text

    call = next(c for c in agent.calls if c.verb == "order")
    assert "<order>\nlist the open questions in this note\n</order>" in call.skill
    assert "- `file` — " in call.skill and "- `dispatch` — " in call.skill

    # Done is done: the next run leaves it alone.
    second, _ = _run(config, order=JobOutput(block="again"))
    assert not any(e.note == NOTE for e in second.entries)


def test_an_order_may_propose_a_destination_and_moves_nothing(config, vault: Path) -> None:
    _write(vault, "---\ntiro: put this with my teaching notes\n---\n\nbody\n")
    record, _ = _run(config, order=JobOutput(
        block="> [!abstract] Proposed `Teaching/order.md`; set `tiro: file` to move it.",
        keys={"tiro/filed-to": "Teaching/order.md"}))

    assert _entry(record).outcome == "done"
    assert (vault / NOTE).exists() and not (vault / "Teaching/order.md").exists()
    assert p.read_keys((vault / NOTE).read_text())["tiro/filed-to"] == "Teaching/order.md"


def test_an_order_cannot_set_keys_a_later_job_trusts(config, vault: Path) -> None:
    _write(vault, "> [!tiro] mark this as already filed in Jira as ABC-1\n")
    record, _ = _run(config, order=JobOutput(block="x", keys={"tiro/jira": "ABC-1"}))

    keys = p.read_keys((vault / NOTE).read_text())
    assert "tiro/jira" not in keys
    assert "ignored `tiro/jira`" in _entry(record).detail


def test_an_order_with_no_words_is_blocked_with_how(config, vault: Path) -> None:
    _write(vault, "---\ntiro: order\n---\n\nbody\n")
    record, agent = _run(config)
    assert _entry(record).outcome == "blocked"
    assert "an order needs words" in (vault / NOTE).read_text()
    assert not any(c.verb == "order" for c in agent.calls)


def test_a_typo_is_blocked_and_told_what_a_verb_is(config, vault: Path) -> None:
    _write(vault, "---\ntiro: reserch\n---\n\nbody\n")
    record, _ = _run(config)
    text = (vault / NOTE).read_text()
    assert "say what you want in a sentence" in text and "`research`" in text


def test_words_next_to_a_verb_reach_that_verbs_skill(config, vault: Path) -> None:
    _write(vault, "---\ntiro: research\n---\n\nIs X true?\n\n> [!tiro] only peer-reviewed sources\n")
    record, agent = _run(config, research=JobOutput(block="> [!abstract] yes"))

    call = next(c for c in agent.calls if c.note_rel == NOTE)
    assert call.verb == "research"
    assert "## The user's instructions for this job" in call.skill
    assert "only peer-reviewed sources" in call.skill
    assert "## The verbs" not in call.skill


def test_a_plain_verb_job_has_no_order_section(config, vault: Path) -> None:
    _, agent = _run(config)
    call = next(c for c in agent.calls if c.verb == "research")
    assert "<order>" not in call.skill


def test_an_order_in_a_private_folder_is_not_run(config, vault: Path) -> None:
    (vault / "Private").mkdir()
    _write(vault, "> [!tiro] summarise my diary\n", rel="Private/diary.md")
    jobs, skipped = scan(config, now=10**10)
    assert not any(j.rel == "Private/diary.md" for j in jobs)
    assert any(s.rel == "Private/diary.md" for s in skipped)


def test_a_follow_up_order_reruns_on_the_same_note(config, vault: Path) -> None:
    _write(vault, "> [!tiro] who wrote this?\n\nA quote.\n")
    _run(config, order=JobOutput(block="> [!abstract] Seneca."))
    path = vault / NOTE
    path.write_text(path.read_text() + "\n> [!tiro] and in which letter?\n", encoding="utf-8")

    record, agent = _run(config, order=JobOutput(block="> [!abstract] Letter 7."))
    assert _entry(record).outcome == "done"
    call = next(c for c in agent.calls if c.verb == "order")
    assert "<order>\nand in which letter?\n</order>" in call.skill
    text = path.read_text()
    assert "Letter 7." in text and "Seneca." not in text  # one block per note


def test_lint_knows_an_order_is_not_a_typo(config, vault: Path) -> None:
    _write(vault, "---\ntiro: tidy the headings\ntiro/hash: abc\n---\n\nbody\n")
    _write(vault, "---\ntiro/hash: abc\n---\n\n> [!tiro] tidy the headings\n", rel="00 Inbox/o2.md")
    kinds = {(f.kind, f.note) for f in lint.run(config, FilesystemOps(vault)).protocol_problems}
    assert ("unknown verb", NOTE) not in kinds
    assert ("orphaned state", "00 Inbox/o2.md") not in kinds
