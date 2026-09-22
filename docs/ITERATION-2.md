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

## Do this first, before anything else in this plan

**The correction log is not implemented.** ITERATION-1 says it "is written from
day one so that `reflect` has data to work with in iteration 2 — logging costs
nothing and the data cannot be recovered retroactively". It was never built:
there is no `corrections` anywhere in `src/`, and no `.tiro/corrections.jsonl`
in the vault.

Every day this stays open is a day of training data that cannot be recovered.
It is perhaps half a day's work and it blocks M2. It should land before the
week of unattended running, not after.

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

**Not in iteration 2:** `connect` (DESIGN §8 puts it in 3), `watch` mode,
`tiro chat`, notifications, embeddings, Jira read-back, any dispatch target
beyond Jira.

## Milestones

### M0 — The correction log (½ day) — *do this now, not in sequence*
Append-only `.tiro/corrections.jsonl`. One line whenever the next run notices
that what Tiro recorded in `tiro/*` and what is true now have diverged:

- `tiro/filed-to` said `uzh/`, the note is in `mch/` → a filing correction.
- Tiro proposed tags, the user removed or changed them → a tagging correction.
- Tiro's block was deleted outright → a rejection, which is the loudest signal
  in the system and currently invisible.

It is a diff between frontmatter and reality, so it costs no model time and no
judgement. Write it from the scan, not from a job, so it is recorded even for
notes nothing is queued against.
- **Done when:** filing a note somewhere other than the proposal produces
  exactly one line, running twice produces no second line, and a week of the
  user's ordinary editing produces no false positives.

### M1 — dispatch, live (½ day)
Nothing to build; everything to verify. Flip `dispatch.live`, file one real
issue from a real spec, and check the crash window against the live instance
rather than a stub.
- **Done when:** one spec becomes exactly one issue; a second run of the same
  note creates nothing; `labels = tiro` returns exactly what was dispatched;
  and iteration 1's criterion 7 can finally be judged.

### M2 — `reflect` (2 days)
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

### M3 — `accept` and `reject` (½ day)
`tiro accept R-019` appends the rule to `.tiro/rules.md` with its date and
provenance. `tiro reject R-019 "reason"` records the reason so the same
proposal is never made twice. Both are promised in ITERATION-1's CLI table and
neither exists.
- **Done when:** an accepted rule is in `rules.md` with provenance and changes
  the next `triage`'s reasoning; a rejected one is never proposed again.

### M4 — Retry after a machine failure (½ day)
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

### M5 — `index` (2 days)
Maintain a Map of Content per area: a note listing what is in a folder, grouped,
with one line each. Rebuild it when the folder's contents change.

This vault is the argument for it — 931 notes and 125 links, so almost nothing
is reachable except by search. An index note per context folder is the cheapest
thing that makes a flat vault navigable, and unlike `connect` it proposes no
relationships it cannot justify from the filesystem.
- **Done when:** `uzh/` has an index that the user would not rewrite; running
  twice changes nothing; adding a note to the folder updates it on the next run
  and the diff is one line.

### M6 — The week (ongoing, from day one)
Iteration 1's M7, unchanged and still owed. Half-hourly timer, daily journal
read, tune budgets and rules.
- **Done when:** seven consecutive days with no manual git intervention, no
  note stuck in `working`, and nothing reverted.

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

**`connect`.** Proposing links between notes is the feature most likely to
produce plausible nonsense at volume, and this vault's 125 links mean there is
no baseline to judge good suggestions against. It waits for `index`.

**`tiro chat`.** The async-via-notes channel is the one that works when the
user is asleep, and it is the one to get right. A synchronous channel is a
different product and would take attention from this one.

**The research block's length.** `rules/output.md` says a block is a handful of
sentences plus sources; the first real research block ran to five paragraphs
and was better for it. One of the two is wrong and a week of real blocks will
say which, so neither changes yet.
