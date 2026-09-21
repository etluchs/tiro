"""The control loop (DESIGN section 3.5).

    lock → pull --rebase → scan → plan → per job [run → apply → gate → commit]
         → journal → push

Per-job commits are the whole safety story: a bad job is one revert away, and it
cannot take a good one with it.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from tiro import gate, journal, protocol
from tiro.agent import AgentError, AgentRunner, JobOutput, JobRequest
from tiro.config import Config
from tiro.ops import OpsUnavailable, VaultOps
from tiro.scan import Job, iter_notes, scan
from tiro.vcs import Git, LockBusy, run_lock


@dataclass
class Outcome:
    outcome: str
    detail: str = ""
    commit: str = ""


def _skill_text(config: Config, verb: str) -> str:
    path = config.root / ".claude" / "skills" / verb / "SKILL.md"
    if not path.exists():
        raise AgentError(f"no skill for verb {verb!r} at {path}")
    return path.read_text(encoding="utf-8")


def _atomic_write(path: Path, text: str, *, keep_mtime: float | None = None) -> None:
    """Temp file plus rename, so Obsidian never sees half a note.

    ``keep_mtime`` restores the modification time afterwards. Bookkeeping writes
    — marking a note `working`, resetting a crashed one — use it so that Tiro's
    own housekeeping does not look like the user typing. Without it the
    skip-if-recently-modified guard fires on Tiro's own edit and the note sits
    out the next minute for no reason.
    """
    tmp = path.with_suffix(path.suffix + ".tiro-tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    if keep_mtime is not None:
        os.utime(path, (keep_mtime, keep_mtime))


def _state_path(config: Config) -> Path:
    return config.tiro_dir / "state.json"


def _load_state(config: Config) -> dict:
    path = _state_path(config)
    if not path.exists():
        return {"attempts": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"attempts": {}}


def _save_state(config: Config, state: dict) -> None:
    path = _state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _attempts_key(job: Job, day: str) -> str:
    return f"{day}|{job.rel}"


def reset_crashed_notes(config: Config, git: Git) -> list[str]:
    """A note left `working` with no live lock is a crashed run, not a busy one."""
    reset: list[str] = []
    for note in iter_notes(config.vault):
        text = note.read_text(encoding="utf-8", errors="replace")
        if protocol.read_keys(text).get("tiro/status") != "working":
            continue
        mtime = note.stat().st_mtime
        _atomic_write(note, protocol.set_key(text, "tiro/status", "queued"),
                      keep_mtime=mtime)
        reset.append(note.relative_to(config.vault).as_posix())
    return reset


def apply_output(
    config: Config,
    job: Job,
    output: JobOutput,
    *,
    run_id: str,
    ops: VaultOps,
    baseline_mtime: float,
) -> tuple[list[str], tuple[str, str] | None]:
    """Serialise the agent's result into the note. Returns (declared, moved).

    This is the only place a note is written. The agent never touches the disk,
    so every byte Tiro adds goes through here and looks the same everywhere.
    """
    note = config.vault / job.rel
    text = note.read_text(encoding="utf-8")

    # The user may have typed while the job ran. Their edit wins; we retry next
    # run. The baseline is the mtime *after* Tiro marked the note `working`, not
    # the one from the scan — otherwise Tiro's own bookkeeping write looks like
    # the user and every job aborts.
    if note.stat().st_mtime != baseline_mtime:
        raise _NoteMovedUnderUs(job.rel)

    text, note_id = protocol.ensure_id(text)
    if output.block.strip():
        text = protocol.upsert_block(text, job=job.verb, id=note_id, body=output.block)
    for key, value in output.keys.items():
        text = protocol.set_key(text, key, value)
    text = protocol.set_key(text, "tiro/status", output.status)
    text = protocol.set_key(text, "tiro/run", run_id)
    # The hash is of the user's content, so it must be computed from the note as
    # it now stands — after our block and keys, which by construction do not
    # affect it.
    text = protocol.set_key(text, "tiro/hash", protocol.user_hash(text))

    _atomic_write(note, text)
    declared = [job.rel]
    moved: tuple[str, str] | None = None

    if job.verb == "file":
        destination = output.keys.get("tiro/filed-to", "").strip()
        if not destination:
            raise AgentError("`file` returned no tiro/filed-to destination")
        # Refuse before touching anything, so a move we would have to undo is
        # never started. The gate checks this again afterwards.
        if not config.trust.permits(job.rel, gate.MOVE_FROM_TRUST):
            raise OpsUnavailable(
                f"cannot move a note out of {job.rel}: that folder is "
                f"{config.trust.level_for(job.rel)}"
            )
        if not config.trust.permits(destination, gate.MOVE_TO_TRUST):
            raise OpsUnavailable(
                f"cannot file into {destination}: that folder is "
                f"{config.trust.level_for(destination)}; raise it to "
                f"{gate.MOVE_TO_TRUST} to let Tiro file there"
            )
        try:
            ops.move(job.rel, destination)
        except Exception:
            # A move that half-happened leaves a copy we made at the
            # destination. Remove that, never the original.
            if (config.vault / destination).exists() and (config.vault / job.rel).exists():
                (config.vault / destination).unlink()
            raise
        declared.append(destination)
        moved = (job.rel, destination)

    return declared, moved


class _NoteMovedUnderUs(Exception):
    pass


def execute_job(
    config: Config,
    git: Git,
    ops: VaultOps,
    agent: AgentRunner,
    job: Job,
    *,
    run_id: str,
) -> Outcome:
    note = config.vault / job.rel

    if not job.valid_verb:
        _block_note(config, job, f"unknown verb `{job.verb}`", run_id)
        _commit_block(git, job, run_id)
        return Outcome("blocked", f"unknown verb `{job.verb}`")

    required = gate.REQUIRED_TRUST[job.verb]
    if not config.trust.permits(job.rel, required):
        level = config.trust.level_for(job.rel)
        detail = f"`{job.verb}` needs {required}; {job.rel} is {level}"
        _block_note(config, job, detail, run_id)
        _commit_block(git, job, run_id)
        return Outcome("blocked", detail)

    before = gate.snapshot(config, git, ops, job.rel)
    text = note.read_text(encoding="utf-8")
    baseline_mtime = note.stat().st_mtime
    _atomic_write(note, protocol.set_key(text, "tiro/status", "working"),
                  keep_mtime=baseline_mtime)

    request = JobRequest(
        verb=job.verb,
        note_rel=job.rel,
        note_text=text,
        skill=_render_skill(config, job, text),
        vault=config.vault,
        max_turns=config.agent.max_turns,
        model=config.agent.model,
        effort=(config.jobs.get(job.verb) or {}).get("effort"),
    )

    try:
        output = agent.run(request)
        declared, moved = apply_output(
            config, job, output, run_id=run_id, ops=ops, baseline_mtime=baseline_mtime
        )
    except _NoteMovedUnderUs:
        git.restore([job.rel])
        return Outcome("skipped", "the user edited the note while we worked on it")
    except (AgentError, OpsUnavailable) as exc:
        git.restore([job.rel])
        _block_note(config, job, str(exc), run_id)
        _commit_block(git, job, run_id)
        return Outcome("blocked", str(exc))

    result = gate.check(
        config, git, ops,
        verb=job.verb, note_rel=job.rel, declared=declared,
        before=before, moved=moved,
    )
    if not result.ok:
        gate.rollback(git, declared, created=[moved[1]] if moved else [])
        _block_note(config, job, "; ".join(result.failures), run_id)
        _commit_block(git, job, run_id)
        return Outcome("blocked", "gate: " + "; ".join(result.failures))

    summary = output.detail or f"{job.verb} {job.rel}"
    sha = git.commit(
        declared,
        f"tiro({job.verb}): {Path(job.rel).stem}\n\n{summary}",
        _trailers(run_id, job),
    )
    return Outcome(output.status, output.detail, sha or "")


def _commit_block(git: Git, job: Job, run_id: str) -> None:
    """Record a blocked note, if it is there to record. A note that vanished
    under us is the user's business, not a commit."""
    if (git.repo / job.rel).exists():
        git.commit([job.rel], f"tiro: block {job.rel}", _trailers(run_id, job))


def _trailers(run_id: str, job: Job) -> dict[str, str]:
    return {"Tiro-Run": run_id, "Tiro-Job": job.verb, "Tiro-Note": job.rel}


def _render_skill(config: Config, job: Job, text: str) -> str:
    rules = config.tiro_dir / "rules.md"
    return (
        _skill_text(config, job.verb)
        + "\n\n---\n\n## This job\n\n"
        + f"Note: `{job.rel}`\nVault root: `{config.vault}`\n"
        + (f"Vault filing rules: `{rules.relative_to(config.vault)}` — read it first.\n"
           if rules.exists() else "")
        + "\nThe note as it stands:\n\n<note>\n"
        + text
        + "\n</note>\n"
    )


def _block_note(config: Config, job: Job, why: str, run_id: str) -> None:
    note = config.vault / job.rel
    if not note.exists():
        return
    text = note.read_text(encoding="utf-8")
    text, note_id = protocol.ensure_id(text)
    text = protocol.upsert_block(
        text, job=job.verb, id=note_id,
        body=f"> [!failure] Tiro · {job.verb} · blocked\n> {why}",
    )
    text = protocol.set_key(text, "tiro/status", "blocked")
    text = protocol.set_key(text, "tiro/run", run_id)
    text = protocol.set_key(text, "tiro/hash", protocol.user_hash(text))
    _atomic_write(note, text)


def once(
    config: Config,
    *,
    agent: AgentRunner,
    ops: VaultOps,
    git: Git | None = None,
    now: float | None = None,
) -> journal.RunRecord:
    git = git or Git(config.vault)
    run_id = journal.new_run_id()
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    record = journal.RunRecord(run_id=run_id, started=started, ops_backend=ops.name)

    with run_lock(config.tiro_dir / "lock"):
        ok, detail = git.pull_rebase()
        if not ok:
            record.note_line(f"**run aborted** — could not rebase onto the remote: {detail}")
            record.finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
            journal.write_journal(config, record)
            journal.write_run_record(config, record)
            return record

        for rel in reset_crashed_notes(config, git):
            record.note_line(f"reset `{rel}` from `working` to `queued` after a crashed run")

        jobs, skipped = scan(config, now=now)
        record.skipped = [{"rel": s.rel, "why": s.why} for s in skipped]

        state = _load_state(config)
        day = run_id[:10]
        deadline = time.monotonic() + config.run.max_seconds

        for job in jobs[: config.run.max_jobs]:
            if time.monotonic() > deadline:
                record.note_line("stopped early: the run's time budget ran out")
                break
            key = _attempts_key(job, day)
            attempts = int(state["attempts"].get(key, 0))
            if attempts >= config.run.max_attempts_per_note_per_day:
                record.skipped.append({"rel": job.rel, "why": f"{attempts} attempts today already"})
                continue
            state["attempts"][key] = attempts + 1
            _save_state(config, state)

            outcome = execute_job(config, git, ops, agent, job, run_id=run_id)
            record.add(journal.Entry(job.verb, job.rel, outcome.outcome,
                                     outcome.detail, outcome.commit))

        journal.write_questions(config)
        record.finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
        journal.write_journal(config, record)
        journal.write_run_record(config, record)
        git.commit(
            ["Tiro", ".tiro"],
            f"tiro(journal): run {run_id}",
            {"Tiro-Run": run_id, "Tiro-Job": "journal"},
        )

        if config.run.push and git.has_remote():
            ok, detail = git.push()
            if not ok:
                record.note_line(f"could not push: {detail}")

    return record
