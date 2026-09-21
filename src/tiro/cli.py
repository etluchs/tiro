"""The command line.

argparse rather than a CLI framework: this has to run unattended from a timer
on someone's laptop, and every dependency between it and the standard library is
one more thing that can be missing at 3am.

Every command is read-only unless it says otherwise, and `--dry-run` on `once`
does the scan and the planning and then stops.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tiro import journal, lint, ops as ops_mod, protocol, runner, scan
from tiro.config import TRUST_MEANING, Config, ConfigError
from tiro.jira import Acli
from tiro.vcs import Git, LockBusy


def _config(args: argparse.Namespace) -> Config:
    return Config.load(root=Path(args.root) if args.root else None,
                       vault=Path(args.vault) if args.vault else None)


def _ops(config: Config, args: argparse.Namespace):
    return ops_mod.for_vault(config.vault, backend=getattr(args, "backend", "auto"))


# -- commands --------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> int:
    """What Tiro can see and what it would do. Touches nothing."""
    config = _config(args)
    git = Git(config.vault)

    print(f"vault    {config.vault}")
    print(f"repo     {config.root}")

    if not git.is_repo():
        print("git      NOT A REPOSITORY — Tiro will not run without one")
    else:
        dirty = git.dirty_paths()
        print(f"git      {git.head()[:10]} on {git('rev-parse', '--abbrev-ref', 'HEAD').strip()}"
              f", {len(dirty)} uncommitted path(s)")
        if config.run.push:
            ok, detail = git.can_push()
            print(f"push     {'ok' if ok else 'NO — ' + detail}")

    probe = ops_mod.probe_obsidian()
    print(f"obsidian {'ok — ' + probe.detail if probe.usable else 'unavailable — ' + probe.detail}")
    if not probe.usable:
        print("         (the filesystem backend will be used; `file` is refused)")

    acli = Acli(site=config.dispatch.site)
    if not acli.installed:
        print("acli     not installed")
    else:
        ok, detail = acli.authenticated()
        print(f"acli     {'authenticated' if ok else 'NOT authenticated'} — {detail}")
    print(f"dispatch project={config.dispatch.project or '(unset)'} "
          f"live={'yes' if config.dispatch.live else 'no — preview only'}")

    print(f"trust    default {config.trust.default} ({TRUST_MEANING[config.trust.default]})")
    for folder, level in sorted(config.trust.folders.items()):
        print(f"         {folder:<24} {level}  {TRUST_MEANING[level]}")

    jobs, skipped = scan.scan(config)
    print(f"\nqueue    {len(jobs)} job(s), {len(skipped)} skipped")
    for job in jobs:
        mark = " " if job.valid_verb else "!"
        print(f"  {mark} {job.verb:<9} {job.rel}  ({job.reason})")
    for skip in skipped:
        print(f"    skip      {skip.rel}  ({skip.why})")
    return 0


def cmd_once(args: argparse.Namespace) -> int:
    config = _config(args)
    ops = _ops(config, args)

    if args.dry_run:
        jobs, skipped = scan.scan(config)
        print(f"would run {len(jobs)} job(s) with the {ops.name} backend:")
        for job in jobs[: config.run.max_jobs]:
            print(f"  {job.verb:<9} {job.rel}")
        return 0

    from tiro.agent import ClaudeAgentRunner

    try:
        record = runner.once(config, agent=ClaudeAgentRunner(config), ops=ops)
    except LockBusy as exc:
        print(f"not this time: {exc}")
        return 0

    for entry in record.entries:
        print(f"{entry.outcome:<11} {entry.verb:<9} {entry.note}"
              + (f"  — {entry.detail}" if entry.detail else ""))
    for note in record.notes:
        print(note)
    if not record.entries:
        print("nothing to do")
    return 0


def cmd_lint(args: argparse.Namespace) -> int:
    config = _config(args)
    ops = _ops(config, args)
    report = lint.run(config, ops)
    path = lint.write(config, report)
    for name, value in report.counts.items():
        print(f"{value:>6}  {name}")
    if not report.authoritative:
        print("\nLink numbers are Tiro's own approximation; Obsidian was not reachable.")
    print(f"\nwritten to {path.relative_to(config.vault)}")
    return 0


def cmd_undo(args: argparse.Namespace) -> int:
    """Revert every commit from a run, newest first."""
    config = _config(args)
    git = Git(config.vault)
    shas = git.commits_for_run(args.run_id)
    if not shas:
        print(f"no commits carry Tiro-Run: {args.run_id}")
        return 1
    print(f"reverting {len(shas)} commit(s) from {args.run_id}")
    git.revert(shas)
    print("done — review with `git log` and push when you are happy")
    return 0


def cmd_strip(args: argparse.Namespace) -> int:
    """Remove every trace of Tiro from a note, leaving the user's text."""
    config = _config(args)
    path = config.vault / args.note
    if not path.exists():
        print(f"no such note: {args.note}")
        return 1
    text = path.read_text(encoding="utf-8")
    cleaned = protocol.strip_blocks(protocol.strip_keys(text))
    if cleaned == text:
        print("nothing of Tiro's in that note")
        return 0
    if args.dry_run:
        print(cleaned)
        return 0
    path.write_text(cleaned, encoding="utf-8")
    print(f"stripped {args.note}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Exercise the external command surfaces we could not verify while building.

    The Obsidian CLI and acli command strings in this repo were written from
    published references, not run against the real thing. This finds out which
    of them are wrong in one go, instead of one failed job at a time.
    """
    config = _config(args)
    failures = 0

    print("obsidian")
    probe = ops_mod.probe_obsidian()
    if not probe.usable:
        print(f"  unavailable — {probe.detail}")
        failures += 1
    else:
        from tiro.ops_cli import ObsidianCliOps

        cli = ObsidianCliOps(config.vault)
        for name in ("unresolved", "orphans", "deadends"):
            result = cli.run(name)
            status = "ok" if result.ok else "FAILED"
            if not result.ok:
                failures += 1
            detail = (result.err or result.out).strip().splitlines()
            print(f"  {name:<12} {status}  {detail[0][:100] if detail else ''}")

    print("acli")
    acli = Acli(site=config.dispatch.site)
    if not acli.installed:
        print("  not installed")
        failures += 1
    else:
        ok, detail = acli.authenticated()
        print(f"  auth         {'ok' if ok else 'FAILED'}  {detail}")
        if not ok:
            failures += 1
        elif config.dispatch.project:
            try:
                found = acli.search(f'project = "{config.dispatch.project}" AND labels = "tiro"')
                print(f"  search       ok  {len(found)} issue(s) already labelled tiro")
            except Exception as exc:  # noqa: BLE001 - reporting, not handling
                print(f"  search       FAILED  {exc}")
                failures += 1

    print("\n" + ("everything answered" if not failures
                  else f"{failures} check(s) failed — see src/tiro/ops_cli.py and jira.py"))
    return 1 if failures else 0


# -- entry point -----------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tiro", description=__doc__)
    parser.add_argument("--root", help="the tiro repo (default: cwd)")
    parser.add_argument("--vault", help="override the vault path from tiro.toml")
    parser.add_argument("--backend", choices=["auto", "cli", "fs"], default="auto",
                        help="which vault-ops backend to use")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="what Tiro sees and would do").set_defaults(fn=cmd_status)

    once = sub.add_parser("once", help="one pass over the vault")
    once.add_argument("--dry-run", action="store_true", help="plan, then stop")
    once.set_defaults(fn=cmd_once)

    sub.add_parser("lint", help="vault health report").set_defaults(fn=cmd_lint)
    sub.add_parser("doctor", help="check the external CLIs answer").set_defaults(fn=cmd_doctor)

    undo = sub.add_parser("undo", help="revert a run")
    undo.add_argument("run_id")
    undo.set_defaults(fn=cmd_undo)

    strip = sub.add_parser("strip", help="remove Tiro's marks from a note")
    strip.add_argument("note", help="vault-relative path")
    strip.add_argument("--dry-run", action="store_true")
    strip.set_defaults(fn=cmd_strip)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except ConfigError as exc:
        print(f"configuration: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
