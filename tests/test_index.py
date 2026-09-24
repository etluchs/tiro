"""Folder indexes (ITERATION-2 M5).

The plan's criteria: an index the user would not rewrite; running twice changes
nothing; a note added to the folder updates it on the next run, and the diff is
one line. Plus the courtesies — created only at L3, never recreated once
deleted — and that lint's orphan count does not collapse the day an index
appears.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from tiro import cli, index, lint, protocol, runner
from tiro.agent import JobOutput, ScriptedAgent
from tiro.config import IndexConfig, TrustMap
from tiro.ops_fs import FilesystemOps
from tiro.vcs import Git

TODAY = "2026-09-24"
AREAS = "Areas/Areas — Index.md"


def _config(config, vault: Path, *folders: str, trust: str = '"Areas/" = "L3"\n'):
    path = vault / ".tiro" / "trust.toml"
    path.write_text(path.read_text() + trust, encoding="utf-8")
    return replace(config, trust=TrustMap.load(path), index=IndexConfig(folders=folders))


def _age(vault: Path) -> None:
    for path in vault.rglob("*.md"):
        os.utime(path, (0, 0))


def _once(config):
    _age(config.vault)
    return runner.once(config, agent=ScriptedAgent(
        {"triage": JobOutput(block="y"), "research": JobOutput(block="x")}),
        ops=FilesystemOps(config.vault))


def test_an_index_lists_each_note_with_its_opening_line(config, vault: Path) -> None:
    config = _config(config, vault, "Areas")
    [o] = index.run(config, {}, today=TODAY)
    assert (o.what, o.note) == ("created", AREAS)

    text = (vault / AREAS).read_text()
    assert protocol.read_keys(text)["tiro/index"] == "Areas/"
    assert "- [[Areas/Compute trends|Compute trends]] — Links to the bitter lesson and " \
           "Teaching/HS26/prog-2." in text
    assert "- [[Areas/orphan|orphan]] — Nothing links here and it links nowhere." in text
    assert "[[Areas/Zürich Notiz|Zürich Notiz]]" in text
    assert "Write around this block" in text
    assert AREAS[:-3] not in text.split("tiro:begin")[1]  # does not list itself


def test_running_twice_changes_nothing_and_commits_nothing(config, vault: Path) -> None:
    config = _config(config, vault, "Areas")
    _once(config)
    git = Git(vault)
    head = git.head()
    before = (vault / AREAS).read_bytes()

    record = _once(config)
    assert (vault / AREAS).read_bytes() == before
    assert not any("index" in n for n in record.notes)
    subjects = git("log", "--format=%s", f"{head}..HEAD").splitlines()
    assert not any(s.startswith("tiro(index)") for s in subjects)


def test_a_new_note_is_a_one_line_diff(config, vault: Path) -> None:
    config = _config(config, vault, "Areas")
    _once(config)
    (vault / "Areas/new idea.md").write_text("# new idea\n\nWhat if the index were boring?\n",
                                             encoding="utf-8")
    Git(vault)("add", "-A")
    Git(vault)("commit", "-qm", "user adds a note")

    record = _once(config)
    assert any("updated the index of `Areas/`" in n for n in record.notes)
    diff = Git(vault)("log", "-1", "--format=", "-p", "--grep=^tiro(index)", "--", AREAS)
    changed = [line for line in diff.splitlines()
               if line[:1] in "+-" and not line.startswith(("+++", "---"))]
    assert changed == ["+- [[Areas/new idea|new idea]] — What if the index were boring?"]


def test_a_heading_that_repeats_the_filename_is_skipped() -> None:
    assert index.describe("---\na: b\n---\n# Notes\n\nThe real line.\n", "Notes") == "The real line."
    assert index.describe("", "x") == "(empty)"
    assert index.describe("x" * 200, "y").endswith("…")
    assert len(index.describe("x" * 200, "y")) == index.DESCRIPTION_WIDTH


def test_subfolders_are_grouped_and_daily_notes_counted(config, vault: Path) -> None:
    (vault / "Teaching/2026-09-20.md").write_text("diary\n", encoding="utf-8")
    (vault / "Teaching/2026-09-21.md").write_text("diary\n", encoding="utf-8")
    config = _config(config, vault, "Teaching", trust='"Teaching/" = "L3"\n')
    index.run(config, {}, today=TODAY)
    text = (vault / "Teaching/Teaching — Index.md").read_text()
    assert "### HS26\n- [[Teaching/HS26/prog-2|prog-2]]" in text
    assert "*Plus 2 daily notes, not listed.*" in text
    assert "2026-09-20" not in text


def test_creating_an_index_needs_l3(config, vault: Path) -> None:
    config = _config(config, vault, "Areas", trust="")  # Areas/ is the default, L1
    [o] = index.run(config, {}, today=TODAY)
    assert o.what == "skipped" and "needs L3" in o.detail
    assert not (vault / AREAS).exists()


def test_an_existing_index_is_kept_up_to_date_below_l3(config, vault: Path) -> None:
    """The user made one by hand, with the key: that is the consent."""
    (vault / AREAS).write_text("---\ntiro/index: Areas/\n---\n# My map\n\nMy words.\n",
                               encoding="utf-8")
    config = _config(config, vault, "Areas", trust="")
    [o] = index.run(config, {}, today=TODAY, now=10**10)
    assert o.what == "updated"
    text = (vault / AREAS).read_text()
    assert text.startswith("---\ntiro/index: Areas/\n---\n# My map\n\nMy words.\n")
    assert "[[Areas/orphan|orphan]]" in text


def test_a_private_folder_is_never_indexed(config, vault: Path) -> None:
    (vault / "Private").mkdir()
    (vault / "Private/diary.md").write_text("secret\n", encoding="utf-8")
    config = _config(config, vault, "Private")
    [o] = index.run(config, {}, today=TODAY)
    assert o.what == "skipped" and "L0" in o.detail
    assert not (vault / "Private/Private — Index.md").exists()


def test_a_deleted_index_is_not_recreated_and_that_is_said_once(config, vault: Path) -> None:
    config = _config(config, vault, "Areas")
    _once(config)
    (vault / AREAS).unlink()

    second = _once(config)
    assert not (vault / AREAS).exists()
    assert any("will not recreate the index of `Areas/`" in n for n in second.notes)
    third = _once(config)
    assert not (vault / AREAS).exists()
    assert not any("index" in n for n in third.notes)


def test_what_the_user_writes_around_the_block_survives(config, vault: Path) -> None:
    config = _config(config, vault, "Areas")
    _once(config)
    text = (vault / AREAS).read_text()
    (vault / AREAS).write_text(text + "\nMy own commentary.\n", encoding="utf-8")
    (vault / "Areas/later.md").write_text("Later.\n", encoding="utf-8")

    _once(config)
    text = (vault / AREAS).read_text()
    assert "My own commentary." in text and "[[Areas/later|later]]" in text


def test_an_index_the_user_is_editing_is_left_alone(config, vault: Path) -> None:
    config = _config(config, vault, "Areas")
    state: dict = {}
    index.run(config, state, today=TODAY)
    (vault / AREAS).write_text((vault / AREAS).read_text() + "typing…\n", encoding="utf-8")
    (vault / "Areas/later.md").write_text("Later.\n", encoding="utf-8")
    [o] = index.run(config, state, today=TODAY)
    assert o.what == "skipped" and o.detail == "you are editing it"


def test_a_folder_that_cannot_be_indexed_is_said_once(config, vault: Path) -> None:
    config = _config(config, vault, "Nowhere")
    first = _once(config)
    assert any("no index for `Nowhere/` — no such folder" in n for n in first.notes)
    assert not any("Nowhere" in n for n in _once(config).notes)


def test_indexes_do_not_hide_orphans(config, vault: Path) -> None:
    before = lint._orphans(vault)
    config = _config(config, vault, "Areas")
    index.run(config, {}, today=TODAY)
    assert lint._orphans(vault) == before
    assert "Areas/orphan.md" in before


def test_the_command(config, vault: Path, capsys, monkeypatch) -> None:
    (vault / ".tiro/trust.toml").write_text(
        (vault / ".tiro/trust.toml").read_text() + '"Areas/" = "L3"\n', encoding="utf-8")
    real = cli._config
    monkeypatch.setattr(cli, "_config",
                        lambda args: replace(real(args), index=IndexConfig(folders=("Areas",))))
    assert cli.main(["--root", str(config.root), "--vault", str(vault), "index"]) == 0
    assert f"created   {AREAS}" in capsys.readouterr().out
    assert Git(vault)("log", "-1", "--format=%s") == "tiro(index): Areas/\n"
