"""The protocol tests. The hash-stability ones are load-bearing: if they fail,
Tiro reprocesses its own output forever."""

from __future__ import annotations

import pytest

from tiro import protocol as p

SIMPLE = """---
title: A note
tags: [one, two]
tiro: research
---

The user's prose.
"""

NO_FM = "Just prose, no frontmatter.\n"


# --- the invariant everything rests on -----------------------------------


@pytest.mark.parametrize("text", [SIMPLE, NO_FM, "", "---\n---\nbody\n"])
def test_tiro_output_never_changes_the_user_hash(text: str) -> None:
    before = p.user_hash(text)
    out = p.upsert_block(text, job="research", id="a1", body="> [!abstract] finding")
    out = p.set_key(out, "tiro/status", "done")
    out = p.set_key(out, "tiro/run", "2026-09-21T14-03Z-a4f2")
    out = p.set_key(out, "tiro/hash", before)
    assert p.user_hash(out) == before


def test_removing_a_block_restores_the_hash() -> None:
    with_block = p.upsert_block(SIMPLE, job="research", id="a1", body="x")
    assert p.user_hash(p.remove_block(with_block, "a1")) == p.user_hash(SIMPLE)


def test_rerunning_a_job_replaces_its_block_rather_than_duplicating() -> None:
    once = p.upsert_block(SIMPLE, job="research", id="a1", body="first")
    twice = p.upsert_block(once, job="research", id="a1", body="second")
    blocks = p.find_blocks(twice)
    assert len(blocks) == 1
    assert blocks[0].body == "second"


def test_a_user_edit_does_change_the_hash() -> None:
    edited = SIMPLE.replace("The user's prose.", "The user's prose, with a question.")
    assert p.user_hash(edited) != p.user_hash(SIMPLE)


# --- the one rule ---------------------------------------------------------


def test_needs_work_picks_up_a_new_request() -> None:
    assert p.needs_work(SIMPLE)


def test_needs_work_ignores_a_note_without_a_verb() -> None:
    assert not p.needs_work("---\ntitle: x\n---\n\nbody\n")


def test_needs_work_is_false_once_hashed() -> None:
    done = p.set_key(SIMPLE, "tiro/hash", p.user_hash(SIMPLE))
    assert not p.needs_work(done)


def test_needs_work_returns_when_the_user_edits_after_a_run() -> None:
    done = p.set_key(SIMPLE, "tiro/hash", p.user_hash(SIMPLE))
    done = p.upsert_block(done, job="research", id="a1", body="answer")
    assert not p.needs_work(done)
    followup = done.replace("The user's prose.", "The user's prose. And one more thing?")
    assert p.needs_work(followup)


def test_deleting_the_hash_forces_a_rerun() -> None:
    done = p.set_key(SIMPLE, "tiro/hash", p.user_hash(SIMPLE))
    assert p.needs_work(p.remove_key(done, "tiro/hash"))


# --- frontmatter is edited as text, never re-serialised -------------------


def test_the_users_yaml_is_passed_through_byte_for_byte() -> None:
    messy = "---\nzz_last:   value   # a comment\ntags: [ b , a ]\n'quoted key': 1\n---\n\nbody\n"
    out = p.set_key(messy, "tiro/status", "done")
    for line in ["zz_last:   value   # a comment", "tags: [ b , a ]", "'quoted key': 1"]:
        assert line in out


def test_setting_a_key_twice_replaces_it_in_place() -> None:
    out = p.set_key(p.set_key(SIMPLE, "tiro/status", "working"), "tiro/status", "done")
    assert out.count("tiro/status") == 1
    assert "tiro/status: done" in out


def test_frontmatter_is_created_when_absent() -> None:
    out = p.set_key(NO_FM, "tiro/status", "queued")
    assert out.startswith("---\ntiro/status: queued\n---\n")
    assert "Just prose" in out


def test_refuses_to_write_a_key_that_is_not_ours() -> None:
    with pytest.raises(p.ProtocolError):
        p.set_key(SIMPLE, "title", "hijacked")


def test_stripping_tiro_keys_drops_an_emptied_frontmatter_block() -> None:
    only_ours = p.set_key(NO_FM, "tiro/status", "done")
    assert p.strip_keys(only_ours).startswith("Just prose")


# --- verbs ----------------------------------------------------------------


def test_body_tag_is_read_as_a_request() -> None:
    assert p.verb("#tiro/triage\n\nbody\n") == "triage"


def test_frontmatter_wins_over_a_body_tag() -> None:
    text = "---\ntiro: research\n---\n\n#tiro/triage\n"
    assert p.verb(text) == "research"


def test_an_unknown_verb_is_returned_rather_than_ignored() -> None:
    assert p.verb("---\ntiro: reserch\n---\n\nbody\n") == "reserch"


def test_a_tag_that_merely_looks_like_ours_is_not_a_request() -> None:
    assert p.verb("body mentioning #tiro/researching and #nottiro/research\n") is None


def test_the_loose_tag_form_people_actually_type_is_a_request() -> None:
    """Found in the real vault: `#tiro research.` at the end of a note."""
    assert p.verb("Marimo instead of jupyter\n\n#tiro research.\n") == "research"
    assert p.verb("#tiro   distill\n") == "distill"
    assert p.verb("#tiro\n") is None
    # The loose form only at the end of a line: this is a sentence, not a
    # request to move the note.
    assert p.verb("#tiro file it tomorrow\n") is None
    assert p.verb("remember to #tiro/file this\n") == "file"


def test_changing_the_verb_changes_the_hash() -> None:
    done = p.set_key(SIMPLE, "tiro/hash", p.user_hash(SIMPLE))
    accepted = done.replace("tiro: research", "tiro: file")
    assert p.needs_work(accepted)


def test_editing_the_destination_on_the_note_is_an_edit_too() -> None:
    """Triage writes tiro/filed-to and then records the hash, so its own write
    does not loop; the user changing it afterwards is noticed."""
    filed = p.set_key(SIMPLE, "tiro/filed-to", "Areas/a.md")
    filed = p.set_key(filed, "tiro/hash", p.user_hash(filed))
    assert not p.needs_work(filed)
    assert p.needs_work(p.set_key(filed, "tiro/filed-to", "Areas/b.md"))


def test_a_bare_tiro_tag_is_reported_as_a_near_miss() -> None:
    assert p.looks_like_request("do this #tiro please\n")
    assert p.looks_like_request("#tiro-research\n")
    assert not p.looks_like_request("#tiro research\n")
    assert not p.looks_like_request("nothing to see\n")


def test_tags_are_read_regardless_of_case() -> None:
    assert p.verb("#Tiro research\n") == "research"
    assert p.verb("#TIRO/distill\n") == "distill"


def test_a_tag_inside_code_or_inside_tiros_own_block_is_not_a_request() -> None:
    assert p.verb("try `#tiro research`\n") is None
    assert p.verb("```\n#tiro/research\n```\n") is None
    assert p.verb("<!-- tiro:begin job=triage id=a -->\ntag #tiro file to accept\n"
                  "<!-- tiro:end id=a -->\n") is None
    assert not p.looks_like_request("see `#tiro` in the docs\n")


def test_a_byte_order_mark_does_not_hide_the_frontmatter() -> None:
    text = "﻿---\ntiro: research\n---\n\nbody\n"
    assert p.verb(text) == "research"
    assert p.user_hash(text) == p.user_hash(text[1:])
    # And with no frontmatter, the mark does not end up inside the prose.
    assert "﻿" not in p.set_key("﻿Hello\n", "tiro/id", "abc")


# --- ids ------------------------------------------------------------------


def test_ensure_id_is_stable_across_calls() -> None:
    once, first = p.ensure_id(SIMPLE)
    twice, second = p.ensure_id(once)
    assert first == second
    assert twice == once


# --- malformed input ------------------------------------------------------


def test_an_unterminated_fence_is_treated_as_having_no_frontmatter() -> None:
    text = "---\ntitle: oops\n\nbody with no closing fence\n"
    assert p.read_keys(text) == {}
    assert p.user_hash(text)  # does not raise


def test_an_unclosed_block_marker_is_left_alone() -> None:
    text = SIMPLE + "\n<!-- tiro:begin job=research id=zz -->\nhalf a block\n"
    assert p.find_blocks(text) == []
    assert "half a block" in p.user_content(text)
