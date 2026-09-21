# Iteration 1 — plan

The goal of iteration 1 is **one week of Tiro running unattended against the real
vault without the user losing trust in it.** Not features. Trust. Everything here
is chosen because it either earns trust or is needed to earn it.

Design rationale: [DESIGN.md](DESIGN.md).

## What ships

| | Capability | Why it's in |
|---|---|---|
| **The chassis** | scan → plan → run → gate → commit → push, with lock, budgets and per-job commits | Nothing else is safe without it |
| **The vault-ops adapter** | one interface, two backends: Obsidian CLI when it answers, filesystem when it doesn't | Decides whether `file` and authoritative link data are available at all (DESIGN §3.4) |
| **The protocol** | `tiro:` verbs, `tiro/*` keys, the hash rule, owned blocks, questions | Makes unattended operation possible at all |
| `lint` | read-only vault health report | Zero risk, immediate value, and it proves Tiro can read the vault correctly before it writes to it |
| `triage` | classify inbox notes, propose title/tags/links/destination — **move nothing** | The daily-value job, with the dangerous half removed |
| `file` | apply an accepted triage proposal, rewriting inbound links | Closes the loop on triage under explicit human accept |
| `research` | bounded, cited research into a note | The "wow" job; the one the user will actually tag things for |
| `distill` | summarise a long note into a block | Cheap given `research`'s machinery |
| `spec` | turn a note into a reviewable spec | The differentiator, without needing any external credentials |
| **The surface** | `Tiro/Journal/`, `Tiro/Questions.md`, `Tiro/Health.md` | How the user supervises without reading diffs |
| **The CLI** | `adopt`, `once`, `lint`, `status`, `accept`, `undo`, `strip`, `chat` | `undo` matters more than any feature |

**Not in iteration 1:** `dispatch` (Jira/GitLab/repo seeding), `reflect` (rule
proposals), `index`, `connect`, `watch` mode, notifications, embeddings. The
correction log *is* written from day one so that `reflect` has data to work with
in iteration 2 — logging costs nothing and the data cannot be recovered
retroactively.

## Milestones

### M0 — Skeleton (½ day)
- `pyproject.toml` (Python 3.11+, `claude-agent-sdk`, `typer`, `ruamel.yaml`,
  `pydantic`, `pytest`), `bin/tiro`, `tiro.toml` loader, `--dry-run` everywhere.
- `CLAUDE.md` (constitution), `rules/safety.md`, `rules/protocol.md`,
  `rules/output.md`.
- `.claude/settings.json` with the tool allowlist.
- `runner/ops.py`: the vault-ops interface and its capability probe
  (`obsidian version`, with a timeout — it launches the app if it is not running,
  so the probe must not be the thing that does that unexpectedly).
- **Done when:** `tiro status` prints the resolved vault path, git state, trust
  table, **which ops backend it detected**, and the job queue it *would* run, and
  touches nothing.

### M1 — `tiro adopt` (1 day)
Reads the vault and proposes, never imposes: infers the existing structure
(PARA / Zettelkasten / LYT / bespoke — detect, don't assume), finds the inbox,
drafts `.tiro/rules.md` from what it observes, drafts `.tiro/trust.toml` with
everything at L1 except the inbox, creates `Tiro/`, writes the vault
`.gitignore` additions. Output lands in `.tiro/proposals/adopt/` for the user to
read and move into place.
- **Done when:** run against the real vault, the proposed `rules.md` describes
  the vault's actual conventions well enough that the user edits fewer than five
  lines.

### M2 — Protocol + scanner + gate (2 days) — *the load-bearing milestone*
- `runner/protocol.py`: parse/serialise frontmatter without reordering or
  reformatting the user's YAML; block find/replace by id; **user-content hash**
  (strip `tiro/*` keys and all owned blocks, normalise line endings, hash).
- `runner/scan.py`: walk, read frontmatter only, apply the §4.2 rule, emit jobs.
- `runner/gate.py`: the post-job validator — schema, declared-path containment,
  no deletions, no unauthorised moves, link integrity, mtime unchanged.
- `runner/git.py`: lock, rebase-or-abort, per-job commit with trailers, push,
  `undo`.
- `runner/ops_fs.py` + `runner/ops_cli.py`: the two backends. The CLI backend
  wraps every call in "exit code is always 0" paranoia — parse the output, then
  verify the effect on the filesystem.
- **Done when:** the property test holds — *for any note and any job, running
  the same job twice produces exactly one commit* — and the gate rejects each of
  six hand-built malicious/buggy diffs (deletes a note, edits prose outside a
  block, writes outside declared paths, breaks a wikilink, corrupts frontmatter,
  moves a file from an L1 folder).

### M3 — `lint` (1 day, ½ with the CLI)
Read-only. Broken wikilinks and embeds, orphans, frontmatter violations,
duplicate titles, inbox notes older than N days, notes stuck in `working` or
`blocked`. Writes `Tiro/Health.md` with counts, trend against last run, and the
worst 20 items linked.

With the CLI backend this is largely a formatter over `unresolved`, `orphans`,
`deadends` and `properties format=json`. The filesystem backend keeps its own
resolver and the report states which engine produced it — an approximation that
claims to be authoritative is worse than no report.
- **Done when:** the report's broken-link list matches a manual spot check on the
  real vault; where both backends are available, they agree on the broken-link
  set (any disagreement is a bug in ours, and a useful one to find early);
  running it twice changes only the timestamp.

### M4 — `triage`, `file`, `research`, `distill`, `spec` (3 days)
One skill per verb under `.claude/skills/`, each with: purpose, the rules it must
read first, the exact output block shape, its declared path scope, its budget,
and worked examples.

`file` delegates the move to `obsidian move` and asserts `unresolved` is no
larger afterwards. **We do not write a wikilink rewriter in iteration 1.**
Without the CLI, `file` refuses and leaves the note `needs-input` — taking the
link-rewriting risk only when the tool that owns link semantics is present is
worth more than the convenience, and it removes the most bug-prone file in the
project from the critical path.
- **Done when:** ten real inbox notes triaged end-to-end; the destination
  proposal is one the user agrees with in ≥7 of 10; `research` produces no
  uncited claim across five notes; `file` moves ten notes with `unresolved`
  unchanged, or refuses cleanly on a vault with no CLI.

### M5 — The user-facing surface (1 day)
Journal writer, `Questions.md` index, `run.json` with tokens/cost/durations,
`tiro undo`, `tiro strip`, `tiro accept`.
- **Done when:** a day's run is fully legible from `Tiro/Journal/<date>.md`
  alone — every action, every question, every failure, every note linked — and
  `tiro undo <run-id>` returns the vault to its pre-run state, verified by
  `git diff`.

### M6 — Unattended for a week (ongoing)
systemd timer every 15 min. Daily: read the journal, check for stuck notes, tune
budgets and `rules.md`. Log every correction.
- **Done when:** seven consecutive days with no manual git intervention, no note
  stuck in `working`, and the user has not reverted anything.

**Estimate: 8–9 focused days** (7–8 with the Obsidian CLI available, which pays
for itself in M3 and M4), roughly half of it M2. That ratio is correct —
M2 is the part that makes everything else safe.

## Acceptance criteria for the iteration

1. **Idempotent.** Running `tiro once` twice over an unchanged vault produces a
   second run with zero commits.
2. **Reversible.** Every run is undone by one command, verified by `git diff`
   against the pre-run tree.
3. **Contained.** No commit in the whole week touches user prose or a file
   outside its job's declared paths. Asserted by the gate and verified by a
   script over the week's history.
4. **Non-destructive.** Zero deletions. Zero broken wikilinks introduced —
   measured by `lint`'s broken-link count, which must not rise across the week.
5. **Legible.** Every action appears in the journal with a link to the note.
6. **Bounded.** No run exceeds its wall-clock or cost ceiling; exceeding one ends
   in `blocked`, never a half-written note.
7. **Useful.** By the end of the week the user has tagged at least twenty notes
   of their own accord. If they haven't, the jobs are wrong — and that is the
   real finding.

## Test strategy

- **Fixture vault** in `tests/fixtures/vault/`: a real git repo with wikilinks,
  aliases, heading links, embeds, unicode filenames, a note with no frontmatter,
  a note with malformed YAML, a note with a prompt-injection body.
- **Unit:** hash stability (adding a Tiro block must not change the user-content
  hash — this is the single most important test in the codebase), block
  replacement, frontmatter round-tripping without reformatting, link rewriting.
- **Gate tests:** the six hostile diffs from M2, each asserted to be rejected
  *and* rolled back.
- **Golden-path integration:** fixture vault + recorded agent responses → assert
  the exact commit sequence.
- **Live smoke:** one job against a scratch clone of the real vault, run before
  every release.
- **Injection:** the fixture's hostile note asserts that Tiro treats note content
  as data — the run completes normally and the instruction is ignored.

## Risks

| Risk | Mitigation |
|---|---|
| The hash rule has an edge case and Tiro loops on a note | The run ledger caps processing at 3 attempts per note per day, then `blocked` |
| Triage proposals are mediocre, the user stops trusting them | `triage` proposes rather than acts, so bad proposals cost a glance. The correction log turns them into rule proposals in iteration 2 |
| The user edits a note mid-run | Skip-if-recent + mtime recheck + atomic write (§5.3) |
| Obsidian Git plugin and Tiro fight over the remote | One syncer: rebase at run start, abort on conflict, never merge prose |
| Cost runs away on a busy inbox | Per-run and per-day ceilings; `research` is opt-in per note, never automatic |
| The agent is talked into something by note content | Tool allowlist + `permission_mode="dontAsk"` + the gate. The four "never"s are enforced in code, not in the prompt |
| Tiro and the Obsidian CLI write to the vault at the same time | The CLI runs inside the same run lock; its calls are verified against the filesystem afterwards, never trusted on their exit code |
| The CLI probe launches Obsidian on a machine where nobody wanted it running | Probe with a timeout and treat a slow answer as absent; the probe is in `tiro status`, which the user runs deliberately, before it is in the timer |
| The container can't reach the vault's git remote | `tiro status` checks push access on every run and journals a warning before the first job |

## First three commits

1. `docs/`: this plan, the design, the prior art. *(you are here)*
2. `CLAUDE.md` + `rules/`: the constitution and the protocol spec, as the
   normative text the skills and the runner both cite.
3. M0 skeleton: `tiro status` against the real vault, read-only.
