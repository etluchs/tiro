# Where this stands

Built in one unattended session, against the plan in
[docs/ITERATION-1.md](docs/ITERATION-1.md). **89 tests, all passing** on Python
3.11 with no dependencies beyond PyYAML and pytest.

## What works, and is tested

| | |
|---|---|
| **The protocol** | frontmatter as text (never a YAML round-trip), Tiro-owned blocks, the user-content hash. 24 tests, including the invariant everything rests on: Tiro's own output cannot change the hash |
| **The chassis** | lock → rebase → scan → plan → per-job [run → apply → gate → commit] → journal → push |
| **The gate** | every hostile diff it exists to stop, applied directly to a working tree and refused: deletion, undeclared paths, trust violations, broken links, corrupted frontmatter, unauthorised moves |
| **The agent layer** | read-only on the vault, structured result, swappable for a scripted stand-in — which is how the whole loop is tested without a model |
| **Six skills** | triage, file, research, distill, spec, dispatch |
| **lint** | broken links, orphans, protocol problems, duplicates, stale inbox, stuck notes, with a trend column |
| **dispatch** | payload validation, preview mode, idempotency by frontmatter then JQL label, and the crash window tested by killing the process between create and write-back |
| **The CLI** | `status`, `once`, `lint`, `doctor`, `undo`, `strip` |

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

## Not built

`tiro adopt` (M1), `tiro chat`, `watch` mode, `reflect`, `index`, `connect`.

`adopt` is the gap that matters: it is what reads a real vault and proposes
`.tiro/rules.md` and `.tiro/trust.toml`. Until then those are written by hand —
there is an example in `tiro.toml.example` and the trust ladder is documented in
`rules/safety.md`.

## First run

```sh
pip install -e '.[dev,agent]'
cp tiro.toml.example tiro.toml      # point `vault` at your vault

# In the vault: .tiro/trust.toml. Start conservative.
#   default = "L1"
#   "00 Inbox/" = "L4"     # things are meant to leave the inbox
#   "Tiro/" = "L4"

tiro status     # what it sees, what it would do. Touches nothing.
tiro doctor     # do the Obsidian CLI and acli answer?
tiro lint       # read-only health report to Tiro/Health.md
tiro once --dry-run
```

Then tag one note with `tiro: research`, run `tiro once`, and read
`Tiro/Journal/<today>.md`. `tiro undo <run-id>` reverts everything a run did.

## Three decisions worth knowing about

These were made while building and are not in the design doc:

1. **The agent is read-only on the vault.** It returns a structured result; the
   runner does every write. "Never edit the user's prose" stops being a rule the
   model has to remember and becomes a tool it does not have.
2. **A move needs two permissions.** Taking a note *out of* where the user put it
   is the risky half (L4 on the source); putting one somewhere is ordinary
   creation (L3 on the destination). So the inbox is L4, and filing into an area
   is opt-in per folder.
3. **Rollback never deletes.** An untracked file may be a note the user wrote
   five minutes ago and has not committed, and git cannot tell us which.
