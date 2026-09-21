# Iteration 1 — plan

The goal of iteration 1 is **one week of Tiro running unattended against the real
vault without the user losing trust in it.** Not features. Trust. Everything here
is chosen because it either earns trust or is needed to earn it.

Three decisions are settled and shape what follows: Tiro runs on the **laptop,
beside a live Obsidian** (so the CLI adapter is the expected path and `file`
ships); `dispatch`'s target is **Jira Cloud** (so the note → issue loop closes in
this iteration, under the irreversibility rules in DESIGN §5.5); and it posts
through **`acli`**, which lives in the dev image and holds the credentials Tiro
therefore never touches.

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
| `spec` | turn a note into a reviewable spec | The differentiator — and the human sign-off gate that `dispatch` depends on |
| `dispatch` | create one Jira issue from a signed-off spec | Closes the loop from "a thought in my notes" to "a task an agent can pick up". The only irreversible job in the system, so it is also the most carefully fenced |
| **The surface** | `Tiro/Journal/`, `Tiro/Questions.md`, `Tiro/Health.md` | How the user supervises without reading diffs |
| **The CLI** | `adopt`, `once`, `lint`, `status`, `accept`, `undo`, `strip`, `chat` | `undo` matters more than any feature |

**Not in iteration 1:** GitLab and repo-seeding as dispatch targets, Jira
read-back of any kind, `reflect` (rule proposals), `index`, `connect`, `watch`
mode, notifications, embeddings. The
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
- The tool allowlist, with `Bash(git *)`, `Bash(obsidian *)` and `Bash(acli *)`
  explicitly denied: all three are the runner's, not the agent's.
- **Done when:** `tiro status` prints the resolved vault path, git state, trust
  table, **which ops backend it detected**, whether `acli` is installed and
  authenticated, and the job queue it *would* run — and touches nothing.

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

### M6 — `dispatch` to Jira (1½ days)
The only job that can do something git cannot undo, so it is built in the order
that makes each step verifiable before the next one can hurt:

0. `acli` goes into the dev image.
   `acli jira auth login --site "<site>.atlassian.net" --email "<you>" --token`
   with the token piped in is a setup step, not a Tiro concern — Tiro never sees
   a token. `tiro status` reports whether it is authenticated; it does not
   attempt to authenticate.
1. `runner/jira.py` — a wrapper over two `acli` calls and nothing else:
   `search(jql)` and `create(payload)`, both via `--json`, both verified
   afterwards because the exit codes are undocumented. The payload schema is
   ours, small and validated (project, type, summary, plain-text description,
   labels), with a real `--generate-json` capture from the actual site as the
   fixture. Descriptions go up as plain text; Jira Cloud does the ADF
   conversion, so Tiro never authors ADF.
2. The `dispatch` skill drafts a **payload**, not an issue — summary,
   description, issue type, labels including the note's stable `tiro-<uuid>` — 
   into a preview block. The model never holds a create-issue tool.
3. The idempotency check: `tiro/jira` set → stop; else JQL on the label → adopt
   if found; else create. Key written back and committed as its own commit,
   immediately.
4. Preview mode is the default. `dispatch.live = true` in `tiro.toml` is what the
   user turns on, deliberately, after the previews look right.
- **Done when:** five specs dispatched with five issues created — not six; the
  crash window is tested by killing the process between create and write-back and
  confirming the next run adopts rather than duplicates; a note with `tiro/jira`
  already set is a no-op; a spec that has not been signed off is refused; and
  `acli` is denied to the agent, asserted by a test that the allowlist rejects
  `Bash(acli *)`.

### M7 — Unattended for a week (ongoing)
systemd timer every 15 min. Daily: read the journal, check for stuck notes, tune
budgets and `rules.md`. Log every correction.
- **Done when:** seven consecutive days with no manual git intervention, no note
  stuck in `working`, and the user has not reverted anything.

**Estimate: ~9 focused days.** The Obsidian CLI saves about a day across M3 and
M4; `dispatch` adds about one and a half. M2 is still nearly half the total,
which remains the right ratio — it is what makes everything after it safe.

Sequencing note: M6 can be built during the M7 week, but `dispatch.live` should
not be switched on until the chassis has a few quiet days behind it. The one job
with no undo is not the one to debug on day one. That ratio is correct —
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
7. **No duplicates.** Every dispatched spec corresponds to exactly one Jira
   issue, verified by a JQL search over the `tiro-*` labels at the end of the
   week. This is the one criterion git cannot rescue, so it is checked by hand.
8. **Useful.** By the end of the week the user has tagged at least twenty notes
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
| `dispatch` creates a duplicate issue after a crash or a retry | Frontmatter key first, then a JQL search on the note's `tiro-<uuid>` label, then create. Write-back is its own immediate commit. Tested by killing the process in the window |
| The model is talked into dispatching something that was never signed off | `spec` → `needs-input` → the user writes `tiro: dispatch`. Two keys, and the model holds neither: the runner checks the state and the runner does the posting |
| Jira credentials end up in the vault or a commit | `acli` holds the credentials; Tiro never reads, stores or passes a token. The pre-commit grep for token shapes stays as belt-and-braces |
| `acli`'s `--from-json` schema turns out to differ from what we assumed | Pin a small payload schema we validate ourselves, with a `--generate-json` capture from the real instance as the fixture. A schema mismatch then fails in a test, not against live Jira |
| The container can't reach the vault's git remote | `tiro status` checks push access on every run and journals a warning before the first job |

## First three commits

1. `docs/`: this plan, the design, the prior art. *(you are here)*
2. `CLAUDE.md` + `rules/`: the constitution and the protocol spec, as the
   normative text the skills and the runner both cite.
3. M0 skeleton: `tiro status` against the real vault, read-only — including
   which ops backend it found and whether `acli` is installed and authenticated.
