# Where this stands

Built in one unattended session, against the plan in
[docs/ITERATION-1.md](docs/ITERATION-1.md), then revised against the real
vault. **123 tests, all passing** on Python 3.11+ with no dependencies beyond
PyYAML and pytest.

## What works, and is tested

| | |
|---|---|
| **The protocol** | frontmatter as text (never a YAML round-trip), Tiro-owned blocks, the user-content hash. 24 tests, including the invariant everything rests on: Tiro's own output cannot change the hash |
| **The chassis** | lock → rebase → scan → plan → per-job [run → apply → gate → commit] → journal → push |
| **The gate** | every hostile diff it exists to stop, applied directly to a working tree and refused: deletion, undeclared paths, trust violations, broken links, corrupted frontmatter, unauthorised moves |
| **The agent layer** | read-only on the vault, structured result, swappable for a scripted stand-in — which is how the whole loop is tested without a model |
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

Until the Obsidian CLI is confirmed, the filesystem backend runs instead: `lint`
says in its report that its link numbers are an approximation, and `file`
refuses rather than moving a note without Obsidian to rewrite the links.

One known gap waits on that confirmation: when Obsidian moves a note it
rewrites the inbound links in *other* notes, and the gate will see those as
undeclared changed paths and fail the job. The fix is to declare, or to accept
link-only rewrites in files that link to the moved note; which one depends on
what `obsidian move` actually does, so it is not written yet.

## Not built

`tiro chat`, `watch` mode, `reflect`, `index`, `connect`, and `tiro accept`
for rule proposals.

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

## What the real vault taught

929 notes, 125 links. Two thirds are a Roam import from 2022 to 2024; most of
the rest are daily notes at the root, and a handful of context folders (employer,
project, topic). No inbox folder, no tags in deliberate use, one frontmatter key.
One note already carried `#tiro research.` — the loose form — which the protocol
did not read. It does now.
