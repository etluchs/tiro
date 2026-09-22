"""adopt, against a vault shaped like the real one: daily notes at the root, an
old import, a few context folders, and leftovers."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tiro import adopt, cli
from tiro.config import Config

REPO = Path(__file__).resolve().parents[1]
OLD = 0.0  # 1970: an archive by any measure


@pytest.fixture
def messy(tmp_path: Path) -> Config:
    vault = tmp_path / "messy"
    notes = {
        "2026-09-15.md": "- [x] a task\n",
        "2026-09-16.md": "",
        "2026-09-17.md": "",
        "Garmin Maps.md": "where the maps are\n",
        "Pläne.md": "- [ ] Zahnarzt\n",
        "Copilot feedback.md": "notes on copilot\n",
        "Untitled.md": "",
        "uzh/Admin.md": "admin things\n",
        "uzh/Ragki.md": "a project\n",
        "mch/Kafka.md": "kafka\n",
        "research/Interesting projects.md": "Marimo\n\n#tiro research.\n",
        "Roam/Progstuff-2024/2023-01-14.md": "---\ntitle: \"2023-01-14\"\n---\n  * old\n",
        "Roam/Progstuff-2024/2023-01-15.md": "---\ntitle: \"2023-01-15\"\n---\n  * old\n",
        "Roam/Progstuff-2024/Rust Workshop.md": "---\ntitle: \"Rust Workshop\"\n---\n  * old\n",
    }
    for rel, text in notes.items():
        p = vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    (vault / "Untitled 1.base").write_text("{}", encoding="utf-8")
    (vault / ".obsidian").mkdir()
    (vault / ".obsidian/community-plugins.json").write_text('["obsidian-tasks-plugin"]')
    for p in (vault / "Roam").rglob("*.md"):
        os.utime(p, (OLD, OLD))
    return Config.load(root=REPO, vault=vault)


def test_the_survey_reads_the_shape_of_the_vault(messy: Config) -> None:
    s = adopt.survey(messy)
    assert s.notes == 14
    assert s.daily_home == "/"
    assert s.inbox == "/"
    assert s.archives == ["Roam"]
    assert s.plugins == ["obsidian-tasks-plugin"]
    assert s.empty == 1 and s.empty_daily == 2 and s.untitled == 2
    assert s.frontmatter_keys["title"] == 3


def test_a_loose_tag_is_a_request_and_a_bare_one_is_a_near_miss(messy: Config) -> None:
    s = adopt.survey(messy)
    assert "research/Interesting projects.md" in s.requests
    assert s.near_misses == []
    (messy.vault / "uzh/Admin.md").write_text("admin\n\n#tiro please\n", encoding="utf-8")
    s = adopt.survey(messy)
    assert s.near_misses == ["uzh/Admin.md"]


def test_a_dormant_diary_folder_counts_as_an_archive_even_without_the_name(messy: Config) -> None:
    (messy.vault / "Roam").rename(messy.vault / "Progstuff")
    for i in range(20):
        p = messy.vault / "Progstuff" / f"2022-01-{i + 1:02d}.md"
        p.write_text("old\n", encoding="utf-8")
    for p in (messy.vault / "Progstuff").rglob("*.md"):
        os.utime(p, (OLD, OLD))
    assert adopt.survey(messy).archives == ["Progstuff"]


def test_the_drafts_follow_the_survey(messy: Config) -> None:
    s = adopt.survey(messy)
    trust = adopt.draft_trust(s)
    assert 'default = "L3"' in trust
    assert '"/" = "L4"' in trust
    assert '"Roam/" = "L1"' in trust

    rules = adopt.draft_rules(s, today="2026-09-22")
    assert "Daily notes stay in the vault root" in rules
    assert "`uzh/` (2)" in rules
    assert "`Roam/` is an archive" in rules
    assert "Never file into it" in rules


def test_adopt_writes_only_proposals(messy: Config, capsys) -> None:
    code = cli.main(["--root", str(REPO), "--vault", str(messy.vault), "--backend", "fs", "adopt"])
    out = capsys.readouterr().out
    assert code == 0
    assert "inbox: the vault root" in out
    proposals = messy.vault / ".tiro/proposals/adopt"
    assert (proposals / "rules.md").exists()
    assert (proposals / "trust.toml").exists()
    assert (proposals / "tiro.toml").read_text().strip().endswith('inbox = "/"')
    assert not (messy.vault / ".tiro/rules.md").exists()
    assert not (messy.vault / ".tiro/trust.toml").exists()
    # the vault's own files are untouched
    assert (messy.vault / "Untitled.md").exists()
    assert (messy.vault / "2026-09-16.md").read_text() == ""


def test_adopt_dry_run_writes_nothing(messy: Config, capsys) -> None:
    cli.main(["--root", str(REPO), "--vault", str(messy.vault), "--backend", "fs",
              "adopt", "--dry-run"])
    assert "notes" in capsys.readouterr().out
    assert not (messy.vault / ".tiro").exists()
