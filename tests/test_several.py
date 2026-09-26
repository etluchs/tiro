"""A note that asks for several things gets all of them.

Found in use: a note carried `#tiro/research` on one line and `#tiro/file` at
the end. Only the first was read, the research ran, the note was recorded as
done, and the filing was dropped without a word. The research block had also
replaced the triage block, because every job on a note wrote into one block.
"""

from __future__ import annotations

import os
from pathlib import Path

from tiro import protocol, runner
from tiro.agent import JobOutput, ScriptedAgent
from tiro.ops import OpsDown
from tiro.ops_fs import FilesystemOps
from tiro.scan import scan

DST = "Tiro/filed.md"


class Moving(FilesystemOps):
    can_move = True

    def move(self, src: str, dst: str) -> None:
        (self.vault / dst).parent.mkdir(parents=True, exist_ok=True)
        (self.vault / src).rename(self.vault / dst)


def _age(vault: Path) -> None:
    for path in vault.rglob("*.md"):
        os.utime(path, (0, 0))


def _note(vault: Path, body: str, keys: str = f"tiro/filed-to: {DST}\n") -> Path:
    path = vault / "00 Inbox/two.md"
    path.write_text(f"---\n{keys}---\n\n{body}", encoding="utf-8")
    _age(vault)
    return path


def _agent() -> ScriptedAgent:
    return ScriptedAgent({
        "research": JobOutput(block="> [!abstract] Tiro · research\n> Found it.", detail="r"),
        "file": JobOutput(block="> [!abstract] Tiro · file\n> Filed.", detail="f"),
        "triage": JobOutput(block="y"),
    })


# --- reading the requests ---------------------------------------------------


def test_every_request_is_read_and_file_goes_last() -> None:
    text = "#tiro/file first\n\nthen a question #tiro/research\n\nand #tiro/distill\n"
    assert protocol.verbs(text) == ["research", "distill", "file"]
    assert protocol.verbs("---\ntiro: spec\n---\n\n#tiro/spec #tiro/hold\n") == ["spec"]
    assert protocol.verbs("keep out #tiro/hold\n") == []


def test_a_request_answered_at_this_content_is_not_asked_again() -> None:
    text = "question #tiro/research\n\n#tiro/file\n"
    text = protocol.set_key(text, "tiro/hash", protocol.user_hash(text))
    text = protocol.set_key(text, "tiro/done", "research")
    assert protocol.pending(text) == ["file"]
    # and an edit asks for everything again
    edited = text.replace("question", "a sharper question")
    assert protocol.pending(edited) == ["research", "file"]


def test_a_note_from_before_tiro_done_counts_its_blocks() -> None:
    text = ("question #tiro/research\n\n#tiro/file\n\n"
            "<!-- tiro:begin job=research id=abc -->\nfound\n<!-- tiro:end id=abc -->\n")
    text = protocol.set_key(text, "tiro/hash", protocol.user_hash(text))
    assert protocol.pending(text) == ["file"]
    # with no block at all, the hash alone meant done, as it always did
    bare = protocol.set_key("#tiro/triage\n", "tiro/hash", protocol.user_hash("#tiro/triage\n"))
    assert protocol.pending(bare) == []


def test_marking_done_after_an_edit_forgets_the_old_answers() -> None:
    text = "#tiro/research #tiro/distill\n"
    text = protocol.set_key(text, "tiro/hash", protocol.user_hash(text))
    text = protocol.set_key(text, "tiro/done", "research, distill")
    text = text.replace("#tiro/research", "more #tiro/research")
    assert protocol.read_keys(protocol.mark_done(text, "distill"))["tiro/done"] == "distill"


def test_one_block_per_job_and_an_old_block_is_replaced_in_place() -> None:
    old = "<!-- tiro:begin job=research id=abc -->\nx\n<!-- tiro:end id=abc -->\n"
    assert protocol.block_id(old, "abc", "research") == "abc"
    assert protocol.block_id(old, "abc", "file") == "abc-file"


# --- running them -------------------------------------------------------------


def test_research_then_file_in_one_run(config, vault: Path) -> None:
    rel = "00 Inbox/two.md"
    _note(vault, "A question about Slurm #tiro/research\n\n#tiro/file\n")
    assert [j.verb for j in scan(config)[0] if j.rel == rel] == ["research", "file"]

    record = runner.once(config, agent=_agent(), ops=Moving(vault))

    assert [(e.verb, e.outcome) for e in record.entries if e.note == rel] == [
        ("research", "done"), ("file", "done")]
    assert not (vault / rel).exists()
    text = (vault / DST).read_text()
    assert "Found it." in text and "Filed." in text  # neither replaced the other
    assert protocol.read_keys(text)["tiro/done"] == "research, file"
    _age(vault)
    assert [j for j in scan(config)[0] if j.rel in (rel, DST)] == []


def test_the_note_that_found_this_files_on_the_next_run(config, vault: Path) -> None:
    """Research done under the old rule: a hash, no `tiro/done`, one block
    keyed by the note's id. The `#tiro/file` below it is still owed."""
    rel = "00 Inbox/two.md"
    body = ("A question #tiro/research\n\n#tiro/file\n\n"
            "<!-- tiro:begin job=research id=6634 -->\n> found\n<!-- tiro:end id=6634 -->\n")
    path = _note(vault, body, keys=f"tiro/id: 6634\ntiro/filed-to: {DST}\n")
    text = path.read_text()
    path.write_text(protocol.set_key(text, "tiro/hash", protocol.user_hash(text)))
    _age(vault)

    jobs = [j for j in scan(config)[0] if j.rel == rel]
    assert [(j.verb, j.reason) for j in jobs] == [("file", "asked for and not yet done")]

    runner.once(config, agent=_agent(), ops=Moving(vault))
    text = (vault / DST).read_text()
    assert "> found" in text and "Filed." in text


def test_a_filing_the_machine_could_not_do_is_retried_alone(config, vault: Path) -> None:
    rel = "00 Inbox/two.md"
    _note(vault, "A question #tiro/research\n\n#tiro/file\n")

    class Closed(FilesystemOps):
        def move(self, src: str, dst: str) -> None:
            raise OpsDown("Obsidian is closed")

    record = runner.once(config, agent=_agent(), ops=Closed(vault))
    assert [e.outcome for e in record.entries if e.note == rel] == ["done", "blocked"]
    text = (vault / rel).read_text()
    assert "Found it." in text and "will retry" in text
    _age(vault)
    assert [j.verb for j in scan(config)[0] if j.rel == rel] == ["file"]


def test_attempts_are_counted_per_request(config, vault: Path) -> None:
    _note(vault, "A question #tiro/research\n\n#tiro/distill\n")
    runner.once(config, agent=ScriptedAgent({
        "research": JobOutput(block="r"), "distill": JobOutput(block="d"),
        "triage": JobOutput(block="y")}), ops=FilesystemOps(vault))
    attempts = runner._load_state(config)["attempts"]
    assert {k.split("|", 2)[2] for k in attempts if "two.md" in k} == {"research", "distill"}
