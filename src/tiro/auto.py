"""Auto mode: looking after notes nobody tagged (ITERATION-3).

A note is usually written in a hurry, or in the middle of something else, and
choosing a verb for it is the context switch Tiro exists to save. So in the
folders the user opens up, Tiro looks at every new or changed note without
being asked, triages it, and files it when the filing is obvious.

Three properties carry the safety argument, and each is enforced here or in
the gate rather than asked of the model:

**The runner chooses the verb.** An auto job is always ``triage``. A sentence
in a note cannot start ``research``, let alone ``dispatch``.

**Obvious is checked, not claimed.** The model may say a destination is
obvious and give a basis. The move happens only if the basis holds up when the
runner looks for itself (``move_problem``), and only out of an L4 folder into
an L3 one that already exists: never #2's "an explicit accept", given in
advance, per folder.

**Tiro moves a note at most once, and never against the user.** A note Tiro has
filed, one the user has moved back, and one whose auto job the user undid are
all proposed for, never moved again.
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime
from pathlib import Path, PurePosixPath

from tiro import corrections, gate, protocol
from tiro.config import ROOT, Config, as_vault_path, in_folder
from tiro.scan import DAILY, Job, Skipped, is_daily, iter_notes

#: Leaving an auto folder needs this; it is what never #2 calls an L4 folder.
MOVE_FROM_TRUST = "L4"
#: Writing the block and keys on a note nobody tagged. The tag's "L2 for
#: itself" does not apply: there is no tag.
WRITE_TRUST = "L2"
#: How many notes in the destination a basis by likeness must name.
LIKE_AT_LEAST = 2


# -- state ------------------------------------------------------------------


def _state(state: dict) -> dict:
    return state.setdefault("auto", {})


def since(state: dict) -> float | None:
    """When auto mode was switched on, or None if it is off."""
    value = state.get("auto", {}).get("since")
    return float(value) if value is not None else None


def switch_on(state: dict, now: float | None = None) -> float:
    """Record now as the moment auto mode starts. Already on: keep the original
    moment, so switching it on twice does not hide notes from the first."""
    auto = _state(state)
    if auto.get("since") is None:
        now = time.time() if now is None else now
        auto["since"] = now
        auto["since_text"] = datetime.fromtimestamp(now).isoformat(timespec="minutes")
    return float(auto["since"])


def switch_off(state: dict) -> None:
    auto = _state(state)
    auto.pop("since", None)
    auto.pop("since_text", None)


def mark_looked(state: dict, rel: str, day: str) -> None:
    _state(state).setdefault("looked", {})[rel] = day


def decline(state: dict, rel: str, user_hash: str) -> None:
    """The user undid an auto job on this note. Recorded with the content they
    undid it at: that exact note is not looked at again, and whatever it
    becomes, Tiro does not move it again."""
    _state(state).setdefault("declined", {})[rel] = user_hash


def declined(state: dict, rel: str) -> str | None:
    return state.get("auto", {}).get("declined", {}).get(rel)


# -- which notes ------------------------------------------------------------


def _in_auto_folder(config: Config, rel: str) -> str | None:
    for folder in config.auto_folders():
        if in_folder(rel, folder):
            return folder
    return None


def _daily_date(rel: str) -> date | None:
    stem = PurePosixPath(rel).stem
    if not DAILY.match(stem):
        return None
    try:
        return date.fromisoformat(stem)
    except ValueError:
        return None


def scan(config: Config, state: dict, *, now: float | None = None
         ) -> tuple[list[Job], list[Skipped]]:
    """Every untagged note auto mode should look at now, newest first.

    Skips are returned only for notes that *would* qualify but for something
    passing — the user still typing, a look already today, an undo — so the
    journal can say why. The hundreds of notes that were never candidates are
    passed over in silence: listing the Roam import every hour is not a report.
    """
    started = since(state)
    if started is None:
        return [], []
    now = time.time() if now is None else now
    today = datetime.fromtimestamp(now).date()
    day = today.isoformat()
    settle = config.auto.settle_minutes * 60
    looked = state.get("auto", {}).get("looked", {})

    jobs: list[Job] = []
    skipped: list[Skipped] = []
    for path in iter_notes(config.vault):
        rel = path.relative_to(config.vault).as_posix()
        folder = _in_auto_folder(config, rel)
        if folder is None:
            continue
        mtime = path.stat().st_mtime
        if mtime < started:
            continue  # written before auto mode was on: never swept
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # the explicit scan reports unreadable notes
        if protocol.verb(text) is not None:
            continue  # the user asked for something, or said hold: theirs
        if not protocol.unseen(text):
            continue
        daily = _daily_date(rel)
        if daily is not None and daily >= today:
            continue  # still being written; looked at the morning after
        if daily is not None and "tiro/hash" in protocol.read_keys(text):
            continue  # a daily note is looked at once, and not again
        if not config.trust.permits(rel, WRITE_TRUST):
            skipped.append(Skipped(rel, f"auto: `{_label(folder)}` is "
                                        f"{config.trust.level_for(rel)}; Tiro may "
                                        f"not write there unasked"))
            continue
        if declined(state, rel) == protocol.user_hash(text):
            skipped.append(Skipped(rel, "auto: you undid Tiro here; edit the note "
                                        "to have it looked at again"))
            continue
        if looked.get(rel) == day:
            skipped.append(Skipped(rel, "auto: looked at once today already"))
            continue
        quiet = now - mtime
        if quiet < settle:
            skipped.append(Skipped(rel, f"auto: settling, last edited "
                                        f"{quiet / 60:.0f} min ago"))
            continue
        if daily is not None:
            reason = "auto: a daily note, the morning after"
        elif "tiro/hash" in protocol.read_keys(text):
            reason = "auto: changed since Tiro last looked"
        else:
            reason = f"auto: new in {_label(folder)}"
        jobs.append(Job(rel=rel, verb="triage", note_id=protocol.note_id(text),
                        hash_before=protocol.user_hash(text), mtime=mtime,
                        reason=reason, trigger="auto"))
    jobs.sort(key=lambda j: j.mtime, reverse=True)
    return jobs, skipped


def _label(folder: str) -> str:
    return "the vault root" if folder == ROOT else f"{as_vault_path(folder).rstrip('/')}/"


# -- whether to move --------------------------------------------------------


def move_problem(config: Config, state: dict, job: Job, text: str,
                 status: str, obvious: bool, basis: dict, destination: str
                 ) -> str | None:
    """Why this auto job must not move its note, or None if it may.

    ``text`` is the note as it was before Tiro wrote to it. Every check is one
    the runner can make itself; the model's word is needed for ``obvious`` and
    is not sufficient for anything.
    """
    rel = job.rel
    keys = protocol.read_keys(text)
    if is_daily(rel):
        return "a daily note stays where it is"
    if status != "done":
        return "triage did not finish with a proposal"
    if not destination:
        return "triage proposed no destination"
    destination = as_vault_path(destination)
    if destination == rel:
        return "it is already where it belongs"
    if not obvious:
        return "triage was not sure enough to move it"
    problem = gate.destination_problem(config, destination)
    if problem:
        return f"`{destination}` is {problem}"
    if not config.trust.permits(rel, MOVE_FROM_TRUST):
        return (f"{_label(_folder_of(rel))} is {config.trust.level_for(rel)}; auto "
                f"mode moves notes only out of L4 folders")
    if not config.trust.permits(destination, gate.MOVE_TO_TRUST):
        return (f"{_label(_folder_of(destination))} is "
                f"{config.trust.level_for(destination)}; filing into it needs "
                f"{gate.MOVE_TO_TRUST}")
    parent = (config.vault / destination).parent
    if not parent.is_dir():
        return f"{_label(_folder_of(destination))} would be a new folder, and that is your call"
    if (config.vault / destination).exists():
        return f"a note is already at `{destination}`"
    if keys.get("tiro/filed"):
        return "Tiro has filed this note once already"
    if declined(state, rel) is not None:
        return "you undid Tiro's work on this note before"
    note_id = keys.get("tiro/id", "")
    if any((note_id and c.note_id == note_id) or c.note == rel
           for c in corrections.read(config)):
        return "you have corrected Tiro on this note before"
    return basis_problem(config, rel, destination, basis)


def basis_problem(config: Config, rel: str, destination: str, basis: dict) -> str | None:
    """Does the reason given for the move check out?

    A rule must exist in ``rules.md``. A likeness must name at least two notes
    that exist, in the destination folder, other than this one.
    """
    rule = str(basis.get("rule") or "").strip()
    if rule:
        rules = config.tiro_dir / "rules.md"
        if not re.fullmatch(r"R-\d+", rule):
            return f"`{rule}` is not a rule id"
        if not rules.exists() or not re.search(
                rf"(?<![\w-]){re.escape(rule)}(?!\d)", rules.read_text(encoding="utf-8")):
            return f"there is no rule {rule} in rules.md"
        return None
    like = basis.get("like")
    if not isinstance(like, list) or not like:
        return "triage gave no basis for the move"
    folder = PurePosixPath(destination).parent
    found = set()
    for item in like:
        other = as_vault_path(str(item))
        if not other.endswith(".md"):
            other += ".md"
        if other == rel or PurePosixPath(other).parent != folder:
            continue
        if (config.vault / other).is_file():
            found.add(other)
    if len(found) < LIKE_AT_LEAST:
        return (f"triage named {len(found)} note(s) like this one in "
                f"{_label(str(folder)) if str(folder) != '.' else 'the root'}, "
                f"and a move needs {LIKE_AT_LEAST}")
    return None


def basis_text(basis: dict) -> str:
    """The reason for a move, as the block says it."""
    rule = str(basis.get("rule") or "").strip()
    if rule:
        return f"by {rule}"
    like = [as_vault_path(str(x)) for x in basis.get("like") or []]
    links = ", ".join(f"[[{x[:-3] if x.endswith('.md') else x}]]" for x in like[:3])
    return f"alongside {links}"


def _folder_of(rel: str) -> str:
    parent = str(PurePosixPath(as_vault_path(rel)).parent)
    return ROOT if parent in ("", ".") else parent + "/"


# -- what the note says -----------------------------------------------------


def moved_line(source: str, run_id: str, basis: dict) -> str:
    return (f"*Filed here from `{source}` {run_id[:10]}, unasked, "
            f"{basis_text(basis)}. `tiro undo {run_id}` puts it back; so does "
            f"moving it yourself, and Tiro learns from that.*")


def stayed_line(why: str | None, proposed: str) -> str:
    if proposed and why:
        return (f"*Tiro looked at this unasked and did not move it: {why}. "
                f"To file it at `{proposed}`, add `tiro: file`. To be left "
                f"alone, `tiro: hold`.*")
    return "*Tiro looked at this unasked. To be left alone, add `tiro: hold`.*"


def status_lines(config: Config, state: dict) -> list[str]:
    """For `tiro status`: whether auto mode is on, and where it looks."""
    started = since(state)
    if started is None:
        return ["auto     off — `tiro auto on` to look after new notes unasked"]
    when = state["auto"].get("since_text") or datetime.fromtimestamp(started).isoformat()
    out = [f"auto     on since {when}; settle {config.auto.settle_minutes} min, "
           f"at most {config.auto.max_per_run} per run"]
    for folder in config.auto_folders():
        level = config.trust.level_for(
            "x.md" if folder == ROOT else as_vault_path(folder).rstrip("/") + "/x.md")
        what = ("files and proposes" if config.trust.index(level) >= config.trust.index(MOVE_FROM_TRUST)
                else "proposes only" if config.trust.index(level) >= config.trust.index(WRITE_TRUST)
                else "not looked at")
        out.append(f"         {_label(folder):<24} {level}  {what}")
    return out
