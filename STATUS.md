# Where this stands

Built in one unattended session, against the plan in
[docs/ITERATION-1.md](docs/ITERATION-1.md), then revised against the real
vault. **164 tests, all passing** on Python 3.11+ with no dependencies beyond
PyYAML and pytest.

## What works, and is tested

| | |
|---|---|
| **The protocol** | frontmatter as text (never a YAML round-trip), Tiro-owned blocks, the user-content hash. 24 tests, including the invariant everything rests on: Tiro's own output cannot change the hash |
| **The chassis** | lock → rebase → scan → plan → per-job [run → apply → gate → commit] → journal → push |
| **The gate** | every hostile diff it exists to stop, applied directly to a working tree and refused: deletion, undeclared paths, trust violations, broken links, corrupted frontmatter, unauthorised moves |
| **The agent layer** | read-only on the vault, structured result, swappable for a scripted stand-in — which is how the whole loop is tested without a model. The tool surface is two lists in `agent.py` and is asserted by `test_agent.py`: no `Bash`, no write tools, no settings file read |
| **Six skills** | triage, file, research, distill, spec, dispatch |
| **lint** | broken links, orphans, protocol problems, duplicates, stale inbox, stuck notes, leftovers, requests Tiro could not read, with a trend column. Daily notes are a class of their own and never count as orphans or leftovers |
| **dispatch** | payload validation, preview mode, idempotency by frontmatter then JQL label, and the crash window tested by killing the process between create and write-back |
| **adopt** | read-only survey of a real vault: folders, daily-note home, inbox, archives, keys, tags, plugins, leftovers. Drafts `rules.md`, `trust.toml`, the `[lint]` section and `.gitignore` lines into `.tiro/proposals/adopt/` |
| **The CLI** | `status`, `once`, `lint`, `doctor`, `adopt`, `undo`, `strip` |

## What is written but unverified

Two external command surfaces were written from published references and have
**never been run against the real thing**, because the machine this was built on
has neither Obsidian nor acli:

- `src/tiro/ops_cli.py` — the Obsidian CLI (`unresolved`, `orphans`, `deadends`,
  `backlinks`, `move`)
- `src/tiro/jira.py` — acli (`jira auth status`, `workitem search`,
  `workitem create --from-json`)

Every command string is collected in one `COMMANDS` dict per module, so
correcting one is a one-line edit. **`tiro doctor` exercises them all and
reports exactly which failed**, so confirming the surface is one command rather
than one failed job at a time.

**Confirmed against a live Obsidian and acli, 2026-09-22.** `tiro doctor`
answers on everything it exercises. Getting there found two real faults:

1. The adapter's success test was "stdout non-empty, stderr empty". An old
   installer satisfies that by printing "Your Obsidian installer is out of
   date" and no data, so every query looked fine and returned nothing — which
   made the gate's link-integrity rule a no-op. Obsidian's chatter (startup log
   line, update banner) is now stripped before the answer is judged.
2. `format=json` is a request, not a contract: `unresolved` honours it,
   `orphans` and `deadends` ignore it and print one path per line. Both shapes
   are accepted.

Note the installer and the app package update separately. Obsidian auto-updates
the `.asar` inside `~/Library/Application Support/obsidian/` but never the
`.app` in `/Applications`, so "check for updates" can report you are current
while the binary carrying the CLI is a year old.

`move` ran for the first time on 2026-09-22 and filed a note correctly. It had
no inbound links, though, so it did not exercise the part that matters: when
Obsidian moves a linked note it rewrites the link in every note that pointed at
it, and those notes are not in the job's declared paths. That is now handled —
the backlinks are computed before the move, declared, and judged at the move's
permission rather than the write rule's, since a link rewrite is a consequence
of a move the user authorised rather than Tiro editing someone's prose. And a
gate failure now rolls back everything the job touched, not only what it
declared, because an undeclared change is exactly the case where leaving it in
place does damage. `backlinks` is the one adapter command still never run.

One limitation worth knowing: the CLI's `unresolved` JSON names the broken
target but not the note holding it, so on that backend `lint` cannot say where
a broken link lives. The filesystem backend can, and gives a line number.

## Not built

`tiro chat`, `watch` mode, `reflect`, `index`, `connect`, `tiro accept` and
`tiro reject`. Planned in [docs/ITERATION-2.md](docs/ITERATION-2.md).

The correction log (ITERATION-2 M0) **is** built and running, so evidence has
started accruing for `reflect`. Three jobs have never been run even once: `distill`, `spec`, and `dispatch`
against a live Jira. Until `dispatch` runs, iteration 1's acceptance
criterion 7 cannot be judged.

## First run

```sh
pip install -e '.[dev,agent]'
cp tiro.toml.example tiro.toml      # point `vault` at your vault

tiro adopt      # survey the vault; drafts land in <vault>/.tiro/proposals/adopt/
                # read them, edit, then move rules.md and trust.toml up to .tiro/
tiro status     # what it sees, what it would do. Touches nothing.
tiro doctor     # do the Obsidian CLI and acli answer?
tiro lint       # read-only health report to Tiro/Health.md
tiro once --dry-run
```

Then tag one note with `tiro: research`, run `tiro once`, and read
`Tiro/Journal/<today>.md`. `tiro undo <run-id>` reverts everything a run did.

## Decisions worth knowing about

These were made while building and are not in the design doc:

1. **The agent is read-only on the vault.** It returns a structured result; the
   runner does every write. "Never edit the user's prose" stops being a rule the
   model has to remember and becomes a tool it does not have.
2. **The tag is the consent.** A note carrying `tiro:` is L2 for itself whatever
   its folder says, unless the folder is L0. Before this, the default ladder
   blocked every request outside a listed folder, and the real vault has almost
   no folders to list.
3. **A move needs two permissions, and `tiro: file` is one of them.** Taking a
   note out of where the user put it is the risky half, but every move today is
   one the user asked for on the note, so the source need only be above L0.
   Putting one somewhere is creation, so the destination must be L3. The
   destination folder may be new.
4. **The agent may not write `tiro`.** The verb is the user's key; if a skill
   could set it, `spec` could queue its own `dispatch`.
5. **Rollback never deletes.** An untracked file may be a note the user wrote
   five minutes ago and has not committed, and git cannot tell us which.
6. **The root can be the inbox.** The trust key `"/"` names files directly in
   the vault root. Daily notes (`YYYY-MM-DD`) are a class: never filed, never
   stale, never orphans, and an empty one is the plugin's doing.
7. **The verb is inside the hash.** It was stripped with the other `tiro*` keys,
   so changing `tiro: triage` to `tiro: file` never re-triggered and the whole
   filing flow was unreachable. Only the `tiro/*` state keys are outside it.
8. **`file` goes where the note says.** The destination is the `tiro/filed-to`
   that was on the note when the user wrote `tiro: file`; the skill may confirm
   it, and a different answer blocks the job.
9. **`.claude/settings.json` is not Tiro's policy.** It configures Claude Code
   sessions a human opens in this repo. Tiro reads no settings file at all, and
   its tool surface lives in `agent.py` (DESIGN §3.6). The docs used to conflate
   the two, which made the deny list there look load-bearing when it is not.

## What the real vault taught

929 notes, 125 links. Two thirds are a Roam import from 2022 to 2024; most of
the rest are daily notes at the root, and a handful of context folders (employer,
project, topic). No inbox folder, no tags in deliberate use, one frontmatter key.
One note already carried `#tiro research.` — the loose form — which the protocol
did not read. It does now.
