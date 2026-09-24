# Iteration 2 — plan

Iteration 1's goal was **one week unattended without the user losing trust**.
The build for it is done; the week started on 2026-09-22 and has not happened
yet. This plan therefore has two halves: finish iteration 1 by living through
it, and build the half of the README that iteration 1 deliberately left out.

**The goal of iteration 2 is that the vault gets better on its own.** Iteration
1 made Tiro safe to leave running. It does not yet learn anything. Every rule in
`.tiro/rules.md` was drafted once by `adopt` and has not changed since, and when
the user overrules a proposal that correction goes nowhere. Closing that loop is
what the README meant by "an evolving set of rules", and it is the only part of
the design that makes Tiro more than a skill pack.

Design rationale: [DESIGN.md](DESIGN.md) §7. What shipped: [ITERATION-1](ITERATION-1.md).

## Where it stands — 2026-09-24

Everything that can be built is built: M0, M2, M3, M4 and M5, and `connect`,
which was moved in from iteration 3 at the user's request (see M7). **What is
left is the user's:** M1 needs a real Jira site and a real spec, and M6 is a
week of living with it. Neither can be done by writing code, and until both
have happened iteration 1's criteria 7 and 8 and this plan's criteria 1 and 4
cannot be judged.

## What ships

| | Capability | Why it's in |
|---|---|---|
| **The correction log** | observe where the user overruled Tiro, append one line | `reflect` has nothing to read without it, and the data is unrecoverable |
| `reflect` | weekly: read the log, propose rule changes | The evolving half of "organise by an evolving set of rules" |
| `accept` / `reject` | the user's half of a rule proposal | A proposal nobody can accept is a suggestion box |
| **dispatch, live** | flip `dispatch.live`, file real Jira issues | Built, fenced, and never once run. Iteration 1's acceptance criterion 7 cannot be judged until it has been |
| `index` | maintain a Map of Content per area | The first job that makes an *unlinked* vault more navigable, which this vault badly is |
| **Retry after a machine failure** | a note blocked by a dead network retries itself | Found in use: an environmental block is indistinguishable from a real one and needs hand-editing |
| **A week of real running** | M7 from iteration 1, actually lived | The point of iteration 1, still outstanding |

**Not in iteration 2:** `watch` mode,
`tiro chat`, notifications, embeddings, Jira read-back, any dispatch target
beyond Jira.

## Milestones

### M0 — The correction log — **done, 2026-09-22**
Append-only `.tiro/corrections.jsonl`, written from the scan before any job
runs, so what it sees is the user's doing rather than this run's. Three
divergences shipped, each a diff between a record Tiro made and what is true:

- `overrode-proposal` — triage proposed a destination, `tiro/filed-to` now says
  somewhere else. The proposal is remembered in `state.json` when it is made,
  because the key it lives in is one the user may overwrite.
- `moved-after-filing` — `file` recorded where it put the note in a new
  `tiro/filed` key, and the note is elsewhere now.
- `rejected-block` — Tiro wrote a block and it is gone. Keyed on a record that
  a block was written, not on `tiro/id`, which is assigned on first touch.

**Tags were dropped from this milestone.** "The user never applied the tags" and
"the user applied them and took them off" are indistinguishable without history,
and only the second is a correction. Guessing would poison the log, and the
acceptance criterion below says the log must be honest before it is useful.
- **Done:** filing a note somewhere other than the proposal produces exactly one
  line; running twice produces no second line; a corrupt line is skipped rather
  than fatal. Most of the 13 tests are false-positive cases. The remaining
  question is the one only time answers — whether a week of ordinary editing
  produces a clean log.

### M1 — dispatch, live (½ day) — **the user's**
Nothing to build; everything to verify. Flip `dispatch.live`, file one real
issue from a real spec, and check the crash window against the live instance
rather than a stub.
- **Done when:** one spec becomes exactly one issue; a second run of the same
  note creates nothing; `labels = tiro` returns exactly what was dispatched;
  and iteration 1's criterion 7 can finally be judged.

### M2 — `reflect` (2 days) — **done, 2026-09-24**
Weekly job. Reads `corrections.jsonl`, groups by direction, and where three
consistent corrections agree writes a proposal to `.tiro/proposals/`, an entry
in `Tiro/Proposals.md`, and a line in the journal. Never edits `rules.md`.

The hard part is not the code, it is the threshold. Three corrections in the
same direction is a guess; with real data it may be two, or five, or "three
within a month". Make it configurable and record the number that produced each
proposal so the choice can be revisited from evidence.
- **Done when:** three hand-made corrections in one direction produce one
  proposal naming them; two do not; corrections in opposing directions produce
  none; and the proposal is legible enough that accepting it needs no thought
  about what it means.
- **Shipped:** weekly from the run, or now with `tiro reflect`. A direction is
  Tiro's folder → the user's folder, one per note however often it was moved;
  a rename within a folder is not one. A direction with any evidence against
  it is held back and shown, not proposed. A proposal naming a folder that an
  existing rule also names says "read with R-00x". The threshold is
  `[reflect] threshold`, default 3, and is recorded on every proposal.
  Deleted blocks are counted on the board and make no rule: a deletion says
  "not this", never "instead that".

### M3 — `accept` and `reject` (½ day) — **done, 2026-09-24**
`tiro accept R-019` appends the rule to `.tiro/rules.md` with its date and
provenance. `tiro reject R-019 "reason"` records the reason so the same
proposal is never made twice. Both are promised in ITERATION-1's CLI table and
neither exists.
- **Done when:** an accepted rule is in `rules.md` with provenance and changes
  the next `triage`'s reasoning; a rejected one is never proposed again.
- **Shipped:** as specified. `reject` refuses without a reason. The commit for
  an accept is `tiro accept R-00x: <title>` and is the only commit that touches
  `rules.md`; a test checks that over the history, the way criterion 2 will.

### M4 — Retry after a machine failure (½ day) — **done, 2026-09-24**
A note blocked because the SDK was missing, the network was down or a token
expired is indistinguishable from one blocked because Tiro could not do the
work. Both get a hash and both need hand-editing before anything retries. Found
the hard way on the first real run.

Do not record a hash when the block came from the machine rather than the
material, so the note retries by itself once the machine is fixed. The attempts
cap already stops a loop.
- **Done when:** a note blocked by a missing dependency runs by itself after
  the dependency is installed, with no edit; a note blocked because the agent
  could not do the job does not.
- **Shipped:** a failure is the machine's if it is `AgentUnavailable`,
  `OpsDown`, `JiraDown`, or a connection, timeout or OS error; everything else,
  a bug included, is about the job. The note says "will retry" and carries no
  hash. Obsidian being closed during a night-time `file` is the common case.

### M5 — `index` (2 days) — **done, 2026-09-24**
Maintain a Map of Content per area: a note listing what is in a folder, grouped,
with one line each. Rebuild it when the folder's contents change.

This vault is the argument for it — 931 notes and 125 links, so almost nothing
is reachable except by search. An index note per context folder is the cheapest
thing that makes a flat vault navigable, and unlike `connect` it proposes no
relationships it cannot justify from the filesystem.
- **Done when:** `uzh/` has an index that the user would not rewrite; running
  twice changes nothing; adding a note to the folder updates it on the next run
  and the diff is one line.
- **Shipped:** folders listed under `[index] folders` in `tiro.toml`; the note
  is `<folder>/<name> — Index.md` and carries `tiro/index`. One line per note
  with its own first line (a heading that repeats the filename is skipped),
  subfolders under their own heading, daily notes counted rather than listed.
  No model. Created only where the folder is L3, updated wherever one exists,
  and never recreated once the user deletes it. Each index is its own commit.
  Lint ignores indexes, so the orphan count does not collapse the day one
  appears. `uzh/` is the user's to judge: add it to `[index]`.

### M6 — The week (ongoing, from day one) — **the user's**
Iteration 1's M7, unchanged and still owed. Half-hourly timer, daily journal
read, tune budgets and rules.
- **Done when:** seven consecutive days with no manual git intervention, no
  note stuck in `working`, and nothing reverted.

### M7 — `connect` — **done, 2026-09-24**, moved in from iteration 3
Added at the user's request, against this plan's own advice below. The advice
still stands as a description of the risk; what changed is that the risk is
now handled in code rather than by leaving the feature out:

- **Asked for per note** with `tiro: connect`. No sweep, no schedule.
- **Evidence the runner checks.** Every suggestion carries a verbatim passage
  from this note and one from the target, 20–300 characters each. Both are
  looked for in the user's own text, outside Tiro's blocks. A passage that is
  not there drops the suggestion.
- **Only real, new, quotable targets.** The target must resolve to a note, not
  this one, not already linked, not in an L0 folder.
- **Few.** At most five links and three contradictions.
- **The runner writes the block**, from what survived, and says how many were
  dropped. The agent's own block and keys are discarded. Quotations are
  written as text, so a link inside one cannot break.
- **Proposals, not edits.** Lint does not count a proposed link as a link.
- **Done when:** the user has run it on ten notes and kept at least one link in
  half of them. If most runs drop most suggestions, the prompt is wrong; if the
  user keeps none, the feature is, and it goes back to iteration 3.

**Estimate: ~6 focused days**, plus the week running alongside.

## Acceptance criteria

Iteration 1's eight carry over unchanged — several of them still cannot be
judged, which is the point. Four more:

1. **The log is honest.** Every correction in `corrections.jsonl` is one the
   user actually made. A false positive here teaches Tiro a rule nobody wants,
   which is worse than learning nothing.
2. **Rules change only by acceptance.** No commit in the week modifies
   `.tiro/rules.md` except one carrying `tiro accept` in its message.
3. **A proposal is worth reading.** Each names its corrections by date and note,
   so the user can check the reasoning rather than trust it.
4. **Indexes are boring.** An index note that changes when the folder did not is
   a bug; so is one the user feels the need to rewrite.

## Risks

| Risk | Mitigation |
|---|---|
| The correction log fires on the user's ordinary edits and learns nonsense | Observe only the specific divergences in M0, never infer intent. Before `reflect` reads it, spend a week reading the log by hand |
| Three corrections is the wrong threshold | Configurable, and every proposal records the number and the corrections behind it |
| `reflect` proposes rules that contradict existing ones | A proposal that conflicts with a rule in `rules.md` names the conflict and asks, rather than proposing a silent replacement |
| `index` becomes a second source of truth that drifts from the vault | It is generated, marked as Tiro's block, and never hand-edited; `tiro strip` removes it |
| dispatch live creates duplicates on the real instance | The idempotency path is already tested against a stub and the crash window is covered; M1 repeats both against the real site before volume |
| The week reveals the jobs are wrong | That is criterion 8 doing its job. If the user has not tagged twenty notes by choice, build nothing from this plan and find out why first |

## What this plan deliberately leaves alone

**`connect`** — *overtaken: built as M7 at the user's request.* Proposing
links between notes is the feature most likely to produce plausible nonsense at
volume, and this vault's 125 links mean there is no baseline to judge good
suggestions against. That is why M7 checks every quotation and caps the count.

**`tiro chat`.** The async-via-notes channel is the one that works when the
user is asleep, and it is the one to get right. A synchronous channel is a
different product and would take attention from this one.

**The research block's length.** `rules/output.md` says a block is a handful of
sentences plus sources; the first real research block ran to five paragraphs
and was better for it. One of the two is wrong and a week of real blocks will
say which, so neither changes yet.
