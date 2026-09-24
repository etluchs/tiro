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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from tiro import corrections, gate, journal, protocol, reflect
from tiro.agent import AgentError, AgentRunner, JobOutput, JobRequest, Usage
from tiro.config import Config
from tiro.failure import is_machine
from tiro.jira import Acli, JiraError, build_payload, note_label
from tiro.links import Index
from tiro.ops import OpsUnavailable, VaultOps
from tiro.scan import Job, iter_notes, scan
from tiro.undo import Undo
from tiro.vcs import Git, LockBusy, run_lock


@dataclass
class Outcome:
    outcome: str
    detail: str = ""
    commit: str = ""
    usage: Usage = field(default_factory=Usage)
    #: ids this job wrote a block for, so a later deletion is a correction.
    wrote_block: list[str] = field(default_factory=list)


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
        return {"attempts": {}, "spend": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"attempts": {}, "spend": {}}
    state.setdefault("attempts", {})
    state.setdefault("spend", {})
    return state


def _save_state(config: Config, state: dict) -> None:
    path = _state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _attempts_key(job: Job, day: str) -> str:
    return f"{day}|{job.rel}"


def _spend_today(state: dict, day: str) -> dict:
    return state.setdefault("spend", {}).setdefault(
        day, {"cost_usd": 0.0, "tokens": 0, "jobs": 0}
    )


def record_spend(state: dict, day: str, usage: Usage) -> None:
    today = _spend_today(state, day)
    today["cost_usd"] = round(today["cost_usd"] + (usage.cost_usd or 0.0), 6)
    today["tokens"] += usage.total_tokens
    today["jobs"] += 1


def over_budget(config: Config, state: dict, day: str) -> str | None:
    """Why today's budget is spent, or None.

    Checked before each job rather than after, because a job's cost is not
    knowable in advance. So the ceiling is "stop once past it", which can
    overshoot by one job — bounded by that job's own turn and time limits, and
    far better than a timer with no ceiling at all.
    """
    today = _spend_today(state, day)
    ceiling = config.run.max_cost_usd_per_day
    if ceiling is not None and today["cost_usd"] >= ceiling:
        return f"today's cost ceiling is spent: ${today['cost_usd']:.2f} of ${ceiling:.2f}"
    ceiling = config.run.max_tokens_per_day
    if ceiling is not None and today["tokens"] >= ceiling:
        return (f"today's token ceiling is spent: {today['tokens']:,} of "
                f"{ceiling:,}")
    return None


def reset_crashed_notes(config: Config, git: Git) -> list[str]:
    """A note left `working` with no live lock is a crashed run, not a busy one."""
    reset: list[str] = []
    for note in iter_notes(config.vault):
        text = note.read_text(encoding="utf-8", errors="replace")
        if protocol.read_keys(text).get("tiro/status") != "working":
            continue
        if config.trust.level_for(note.relative_to(config.vault).as_posix()) == "L0":
            continue  # the folder was closed after the crash; leave it be
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
    created_dirs: list[Path] | None = None,
    rewritten: set[str] | None = None,
    wrote_block: list[str] | None = None,
    undo: Undo | None = None,
) -> tuple[list[str], tuple[str, str] | None]:
    """Serialise the agent's result into the note. Returns (declared, moved).

    This is the only place a note is written. The agent never touches the disk,
    so every byte Tiro adds goes through here and looks the same everywhere.

    ``created_dirs``, if given, collects directories made for a move, so that a
    gate failure can take them away again along with the moved copy. Every file
    this writes, or that the move changes, is recorded in ``undo``.
    """
    undo = Undo(config.vault) if undo is None else undo
    note = config.vault / job.rel
    text = note.read_text(encoding="utf-8")

    # The user may have typed while the job ran. Their edit wins; we retry next
    # run. The baseline is the mtime *after* Tiro marked the note `working`, not
    # the one from the scan — otherwise Tiro's own bookkeeping write looks like
    # the user and every job aborts.
    if note.stat().st_mtime != baseline_mtime:
        raise _NoteMovedUnderUs(job.rel)

    text, note_id = protocol.ensure_id(text)
    # Read before the skill's keys land on the note: this is the destination
    # the user accepted, and the skill's is only allowed to agree with it.
    accepted = protocol.read_keys(text).get("tiro/filed-to", "").strip()

    if job.verb == "dispatch":
        # Before the note is written, because the issue key is one of the
        # things being written. A failure here leaves the note untouched.
        output = _dispatch(config, job, output, run_id=run_id, note_id=note_id)

    if output.block.strip():
        text = protocol.upsert_block(text, job=job.verb, id=note_id, body=output.block)
        if wrote_block is not None:
            wrote_block.append(note_id)
    for key, value in output.keys.items():
        text = protocol.set_key(text, key, value)
    text = protocol.set_key(text, "tiro/status", output.status)
    text = protocol.set_key(text, "tiro/run", run_id)
    # The hash is of the user's content, so it must be computed from the note as
    # it now stands — after our block and keys, which by construction do not
    # affect it.
    text = protocol.set_key(text, "tiro/hash", protocol.user_hash(text))

    undo.write(job.rel, text)
    declared = [job.rel]
    moved: tuple[str, str] | None = None
    created_dirs = [] if created_dirs is None else created_dirs
    rewritten = set() if rewritten is None else rewritten

    if job.verb == "file":
        # The destination is the one the user accepted: the `tiro/filed-to`
        # that was on the note when they wrote `tiro: file`. The skill may
        # confirm it, not change it — otherwise "the tag is the accept" would
        # accept whatever the model decided afterwards.
        proposed = output.keys.get("tiro/filed-to", "").strip()
        if not accepted:
            raise AgentError(
                "`file` needs a tiro/filed-to on the note to accept; run triage "
                "first, or write the destination yourself"
            )
        if proposed and proposed != accepted:
            raise AgentError(
                f"the note accepts `{accepted}` but the skill returned "
                f"`{proposed}`; the note wins — edit it if you meant the other"
            )
        destination = accepted
        # Refuse before touching anything, so a move we would have to undo is
        # never started. The gate checks all of this again afterwards.
        problem = gate.destination_problem(config, destination)
        if problem:
            raise OpsUnavailable(f"cannot file to {destination}: {problem}")
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
        # A destination folder that does not exist yet is fine: a vault without
        # structure grows its folders one filing at a time. But an empty folder
        # is visible in Obsidian, so one we made for a move that then failed is
        # taken away again — only one we made, and only while it is empty.
        # Which notes point at this one, worked out *before* the move, because
        # afterwards there is nothing left to point at. Obsidian rewrites each
        # of them as part of the move, so they are paths this job touches and
        # must declare. Computed with our own resolver rather than asked of the
        # backend: it is the same resolver the gate compares links with, and it
        # does not depend on a CLI command that has never run.
        relinked = sorted(Index(config.vault).backlinks(job.rel))
        # Everything the move is about to change, as it is now: the note, the
        # place it is going, and every note whose link Obsidian will rewrite.
        for rel in (destination, *relinked):
            undo.track(rel)
        made = _mkdirs(config.vault, (config.vault / destination).parent)
        try:
            ops.move(job.rel, destination)
        except Exception:
            # A move that half-happened leaves a copy we made at the
            # destination. Remove that, never the original.
            if (config.vault / destination).exists() and (config.vault / job.rel).exists():
                (config.vault / destination).unlink()
            _rmdirs(made)
            raise
        for rel in (job.rel, destination, *relinked):
            undo.settle(rel)
        declared.append(destination)
        declared.extend(relinked)
        rewritten.update(relinked)
        moved = (job.rel, destination)
        created_dirs.extend(made)

        # Record where the note actually landed, distinct from `tiro/filed-to`,
        # which is a proposal the user may edit. The correction log needs to
        # tell "Tiro put it here and the user moved it" from "Tiro suggested
        # here and the user never agreed", and only a record of the former
        # separates them.
        moved_text = (config.vault / destination).read_text(encoding="utf-8")
        undo.write(destination, protocol.set_key(moved_text, "tiro/filed", destination))

    return declared, moved


def _mkdirs(vault: Path, target: Path) -> list[Path]:
    """Create ``target`` and any missing parents inside the vault. Returns the
    directories that did not exist before, deepest first."""
    made: list[Path] = []
    for parent in [target, *target.parents]:
        if parent == vault or parent.exists():
            break
        made.append(parent)
    for path in reversed(made):
        path.mkdir()
    return made


def _rmdirs(made: list[Path]) -> None:
    """Remove directories ``_mkdirs`` created, deepest first, while empty."""
    for path in made:
        try:
            path.rmdir()
        except OSError:
            return


class _NoteMovedUnderUs(Exception):
    pass


def _dispatch(
    config: Config,
    job: Job,
    output: JobOutput,
    *,
    run_id: str,
    note_id: str,
) -> JobOutput:
    """Turn a drafted payload into at most one Jira issue (DESIGN section 5.5).

    The order is the safety property: check the note, then ask Jira, then
    create, then let the caller write the key. Every exit from this function is
    either "an issue already exists and here is its key" or "nothing was
    created".
    """
    if output.status != "done":
        return output  # the agent asked a question; nothing to file

    note = config.vault / job.rel
    existing = protocol.read_keys(note.read_text(encoding="utf-8")).get("tiro/jira")
    if existing:
        output.detail = f"already filed as {existing}"
        return output

    if not config.dispatch.project:
        raise JiraError("no dispatch.project configured; refusing to guess one")

    payload = build_payload(
        output.payload,
        project=config.dispatch.project,
        default_type=config.dispatch.issue_type,
        note_rel=job.rel,
        note_id=note_id,
        run_id=run_id,
    )

    if not config.dispatch.live:
        # Preview: show exactly what would be posted and stop. The user turns
        # on dispatch.live once the payloads look right.
        body = json.dumps(payload.to_json(config.dispatch.project), indent=2)
        output.block = (
            f"{output.block}\n\n"
            "> [!warning] Preview only — nothing has been created.\n"
            "> Set `dispatch.live = true` in `tiro.toml` to file it.\n\n"
            f"```json\n{body}\n```"
        ).strip()
        output.status = "needs-input"
        output.detail = output.detail or "drafted a payload; preview only"
        return output

    acli = Acli(site=config.dispatch.site)
    label = note_label(note_id)
    # Jira has no idempotency key, so this search is ours. If it fails we stop:
    # creating on an unreadable answer is how duplicates happen.
    found = acli.search(f'labels = "{label}" ORDER BY created ASC')
    if found:
        output.keys["tiro/jira"] = found[0]
        output.detail = f"adopted the existing issue {found[0]}"
        return output

    key = acli.create(
        payload,
        project=config.dispatch.project,
        workdir=config.tiro_dir / "runs" / run_id,
    )
    output.keys["tiro/jira"] = key
    output.detail = f"filed as {key}"
    return output


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

    # Trust first, before anything that writes — including the "unknown verb"
    # block below. Only L0 fails this (every verb needs L2, and the tag grants
    # it); the scan already skips L0, so this is the second check of the two.
    # Writing a "blocked" block would be exactly the write L0 forbids, so the
    # refusal goes in the journal alone.
    required = gate.REQUIRED_TRUST.get(job.verb, "L2")
    if not gate.note_permits(config, job.rel, required):
        level = config.trust.level_for(job.rel)
        return Outcome("skipped", f"`{job.verb}` needs {required}; {job.rel} is {level}")

    if not job.valid_verb:
        _block_note(config, job, f"unknown verb `{job.verb}`", run_id)
        _commit_block(git, job, run_id)
        return Outcome("blocked", f"unknown verb `{job.verb}`")

    undo = Undo(config.vault)
    try:
        return _execute(config, git, ops, agent, job, run_id=run_id, undo=undo)
    except Exception as exc:  # noqa: BLE001 - the last line of never #4
        # Anything the handlers below did not expect. The note may have been
        # written and not committed; put back what Tiro changed, say so on the
        # note and in the journal, and let the run go on to the next job.
        undo.undo()
        retry = is_machine(exc)
        detail = f"unexpected failure: {type(exc).__name__}: {exc}"
        _block_note(config, job, detail, run_id, retry=retry)
        _commit_block(git, job, run_id)
        return Outcome("blocked", detail + (" — retries by itself" if retry else ""))


def _execute(
    config: Config,
    git: Git,
    ops: VaultOps,
    agent: AgentRunner,
    job: Job,
    *,
    run_id: str,
    undo: Undo,
) -> Outcome:
    note = config.vault / job.rel
    text = note.read_text(encoding="utf-8")
    baseline_mtime = note.stat().st_mtime
    undo.write(job.rel, protocol.set_key(text, "tiro/status", "working"),
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

    created_dirs: list[Path] = []
    rewritten: set[str] = set()
    wrote_block: list[str] = []
    try:
        output = agent.run(request)
        # The gate's "before" is taken now, not when the job started. The agent
        # holds no write tools, so nothing that changes while it thinks is
        # Tiro's doing — it is the user, working in Obsidian. Snapshotting at the
        # start blamed every such edit on the job, blocked it, and (when rollback
        # still meant `git checkout`) reverted the user's edit.
        before = gate.snapshot(config, git, ops, job.rel)
        declared, moved = apply_output(
            config, job, output, run_id=run_id, ops=ops, baseline_mtime=baseline_mtime,
            created_dirs=created_dirs, rewritten=rewritten, wrote_block=wrote_block,
            undo=undo,
        )
    except _NoteMovedUnderUs:
        # The user is typing in this note. Undo leaves it alone, since it is no
        # longer as Tiro left it; the next run finds it `working`, resets it and
        # tries again.
        undo.undo()
        return Outcome("skipped", "the user edited the note while we worked on it")
    except (AgentError, OpsUnavailable, JiraError) as exc:
        undo.undo()
        retry = is_machine(exc)
        _block_note(config, job, str(exc), run_id, retry=retry)
        _commit_block(git, job, run_id)
        return Outcome("blocked", str(exc) + (" — retries by itself" if retry else ""))

    result = gate.check(
        config, git, ops,
        verb=job.verb, note_rel=job.rel, declared=declared,
        before=before, moved=moved, rewritten=rewritten,
    )
    if not result.ok:
        # Put back what Tiro changed and nothing else. A path the job did not
        # declare changed while Tiro was writing: that may be Obsidian, or the
        # user saving, and the two cannot be told apart — so it is named here
        # and left exactly as it is. A stale link can be fixed; lost prose
        # cannot.
        left = undo.undo()
        _rmdirs(created_dirs)
        untouched = sorted(set(left) | {p for p in result.changed if p not in declared})
        why = "; ".join(result.failures)
        if untouched:
            why += ("; left as found, because Tiro cannot tell its own change "
                    "from yours: " + ", ".join(untouched))
        _block_note(config, job, why, run_id)
        _commit_block(git, job, run_id)
        return Outcome("blocked", "gate: " + why)

    outcome_name = "preview" if (job.verb == "dispatch" and not config.dispatch.live) else output.status
    summary = output.detail or f"{job.verb} {job.rel}"
    sha = git.commit(
        declared,
        f"tiro({job.verb}): {Path(job.rel).stem}\n\n{summary}",
        _trailers(run_id, job),
    )
    return Outcome(outcome_name, output.detail, sha or "", output.usage, wrote_block)


def _commit_block(git: Git, job: Job, run_id: str) -> None:
    """Record a blocked note, if it is there to record. A note that vanished
    under us is the user's business, not a commit."""
    if (git.repo / job.rel).exists():
        git.commit([job.rel], f"tiro: block {job.rel}", _trailers(run_id, job))


def _link(rel: str) -> str:
    return rel[:-3] if rel.endswith(".md") else rel


def _trailers(run_id: str, job: Job) -> dict[str, str]:
    return {"Tiro-Run": run_id, "Tiro-Job": job.verb, "Tiro-Note": job.rel}


def _render_skill(config: Config, job: Job, text: str) -> str:
    rules = config.tiro_dir / "rules.md"
    if rules.exists():
        rules_line = f"Vault filing rules: `{rules.relative_to(config.vault)}` — read it first.\n"
    else:
        rules_line = (
            "This vault has no `.tiro/rules.md` yet. Where a skill tells you to "
            "consult the rules, go by what the vault already does instead: look at "
            "where notes like this one live, and say plainly that the proposal is "
            "inferred rather than rule-backed. A proposal costs the user a glance; "
            "a question costs them a decision.\n"
        )
    return (
        _skill_text(config, job.verb)
        + "\n\n---\n\n## This job\n\n"
        + f"Note: `{job.rel}`\nVault root: `{config.vault}`\n"
        + rules_line
        + "\nThe note as it stands:\n\n<note>\n"
        + text
        + "\n</note>\n"
    )


def _block_note(config: Config, job: Job, why: str, run_id: str, *,
                retry: bool = False) -> None:
    """Say on the note why the job could not finish.

    ``retry`` is for a failure of the machine rather than the material
    (``failure.py``). The hash is then not recorded, so the note still needs
    work and the next run picks it up by itself — the attempts cap stops that
    becoming a loop. Any hash already there is left: it was of older content,
    so it cannot match and cannot stop the retry.
    """
    note = config.vault / job.rel
    if not note.exists():
        return
    text = note.read_text(encoding="utf-8")
    text, note_id = protocol.ensure_id(text)
    if retry:
        body = (f"> [!warning] Tiro · {job.verb} · will retry\n> {why}\n>\n"
                "> This is the machine, not the note: nothing for you to do. "
                "Tiro tries again on its next run.")
    else:
        body = f"> [!failure] Tiro · {job.verb} · blocked\n> {why}"
    text = protocol.upsert_block(text, job=job.verb, id=note_id, body=body)
    text = protocol.set_key(text, "tiro/status", "blocked")
    text = protocol.set_key(text, "tiro/run", run_id)
    if not retry:
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

        # Before any job runs, so what is observed is the user's doing and not
        # this run's. Costs no model time: it is a diff between what Tiro
        # recorded and what is true (DESIGN section 7).
        for correction in corrections.log(config, corrections.observe(config, state)):
            record.note_line(
                f"noted a correction: `{correction.kind}` on [[{_link(correction.note)}]]"
                + (f" — {correction.was} → {correction.now}"
                   if correction.kind != "rejected-block" else " — the block was deleted")
            )
        day = run_id[:10]
        deadline = time.monotonic() + config.run.max_seconds

        for job in jobs[: config.run.max_jobs]:
            if time.monotonic() > deadline:
                record.note_line("stopped early: the run's time budget ran out")
                break
            spent = over_budget(config, state, day)
            if spent:
                record.note_line(f"**stopped early** — {spent}. Nothing further runs "
                                 "until tomorrow; raise `[run]` in `tiro.toml` to change that")
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
                                     outcome.detail, outcome.commit, outcome.usage))
            for block_id in outcome.wrote_block:
                corrections.remember_block(state, block_id, run_id)
            if job.verb == "triage" and outcome.outcome == "done":
                # Remembered now, because the proposal lives in a frontmatter
                # key the user is free to overwrite — and overwriting it is
                # exactly the correction we are here to notice.
                note = config.vault / job.rel
                if note.exists():
                    proposed = protocol.read_keys(
                        note.read_text(encoding="utf-8")).get("tiro/filed-to", "")
                    corrections.remember_proposal(state, job.rel, proposed, run_id)
            # Written immediately, not at the end: a run killed mid-pass must
            # still have spent what it spent as far as tomorrow is concerned.
            record_spend(state, day, outcome.usage)
            _save_state(config, state)

        # Weekly, and after the jobs so this run's own corrections are in the
        # log. No model time: it is counting.
        today = run_id[:10]
        if reflect.due(config, state, today):
            report = reflect.reflect(config, today=today)
            reflect.mark_done(state, today)
            _save_state(config, state)
            for p in report.new:
                record.note_line(
                    f"proposes **{p.id}**: {p.title}, from {len(p.corrections)} "
                    f"corrections — see [[Tiro/Proposals]]; `tiro accept {p.id}` "
                    f"or `tiro reject {p.id} \"why\"`")

        journal.write_questions(config)

        # Push the job commits *before* the journal is written, so that a
        # failure can still be reported in it. It used to happen afterwards,
        # which meant a vault that had quietly stopped syncing said so nowhere:
        # not in the journal, not in run.json, only on a terminal that a timer
        # does not have.
        pushed = True
        if config.run.push and git.has_remote():
            pushed, detail = git.push()
            if not pushed:
                record.note_line(
                    f"**could not push** — {detail}. The work is committed here but "
                    "the remote has not got it; the next run tries again."
                )

        record.finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
        journal.write_journal(config, record)
        journal.write_run_record(config, record)
        git.commit(
            ["Tiro", ".tiro"],
            f"tiro(journal): run {run_id}",
            {"Tiro-Run": run_id, "Tiro-Job": "journal"},
        )
        # And carry the journal itself up, now that it is written. Only worth
        # trying when the first push worked; if it did not, this run has already
        # said so and the next run will carry both.
        if pushed and config.run.push and git.has_remote():
            git.push()

    return record
