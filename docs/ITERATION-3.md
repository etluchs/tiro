# Iteration 3 — plan

Iterations 1 and 2 made Tiro safe and made it learn. Both assume the user
arrives at a note knowing what they want done with it, and says so with a verb.

**Use says otherwise.** A note is usually written in a hurry, or in the middle
of something else. Choosing a verb means asking "what is this, where does it
go, what does it relate to?", and that is the exact context switch Tiro exists
to save. If the user has to remember `tiro: triage` and then come back to
write `tiro: file`, most notes never get either word.

**The goal of iteration 3 is that a note gets looked after without being asked
for.** Tiro looks at every new or changed note in the places the user opens up
and does what is obvious. Where it is unsure, it asks in the note, the same as
now. The explicit protocol stays exactly as it is, for when the user *is*
thinking about how the vault is organised.

The safety argument is the one the user made: every action is one commit, has a
line in the journal, and `tiro undo` reverses it from Tiro's own record. The
things that cannot be undone that way (deleting a note, editing the user's
prose, `dispatch`) stay out of reach.

Design rationale: [DESIGN.md](DESIGN.md). Before this: [ITERATION-2](ITERATION-2.md).

## Where it stands: 2026-09-25

M0 to M4 are built, with 37 tests in `tests/test_auto.py`. **What is left is
the user's:** raise `"/"` (and any other folders) to L4 in `.tiro/trust.toml`,
run `tiro auto on`, and live with it for a week (M5).

Three things differ from the plan below:

- **`tiro undo` had to change.** It is a `git revert`, and a job's commit
  carried the user's uncommitted text with Tiro's, so undo reverted both. On a
  capture that was never committed, it deleted the note. The runner now
  commits a changed note as the user left it before a job touches it, and
  undo no longer reverts the journal commit, which carries `state.json`.
- **Tiro moves a note at most once.** Decision 3 assumed a filed note leaves
  the auto folders. It does not when the destination is also L4. A note
  carrying `tiro/filed` is proposed for, never moved again.
- **Lint's stale-inbox check is unchanged.** Why auto mode did not move a note
  is written on the note itself, and a second copy in `Health.md` can wait
  until the week shows whether anyone looks for it there.

## The shape, in one paragraph

A second trigger, not a new verb. The rule today is "act when `tiro:` is present
and the note changed". Auto mode adds "or the note is in an auto folder and is
new or changed since Tiro last saw it". For such a note **the runner** queues
`triage`; the model never chooses the verb, so a sentence in a note still cannot
start a job. If triage's proposal is obvious by a test the runner can check, the
runner files the note in the same job. Otherwise the block stays as a proposal,
and one word from the user (`tiro: file`) accepts it, as today.

## Decisions

These are made in the plan so the build does not have to make them silently.
Each can be revisited; the ones marked **ask** want the user's word first.

### 1. What auto mode does: triage, and the move when it is obvious

Title, tags, related notes and a destination: triage already works all of these
out, and the related notes answer "does this relate to something else?". What
auto mode adds is doing the move itself.

**Not** `research`, `distill`, `connect`, `spec` or `dispatch`. `research`
costs money per note and brings in web content nobody asked for. `dispatch`
cannot be undone. `spec` exists to be signed. `connect` is the feature most
likely to produce plausible nonsense at volume. Triage may *suggest* one of
them in its block ("reads like a question to look up — add `tiro: research`"),
which is one word rather than a decision.

### 2. When a move counts as obvious

The move needs every one of these, and the runner checks each rather than
taking the model's word:

- The source folder is **L4**. This is what never #2 already says ("never move
  outside an L4 folder without an explicit accept"), and `safety.md` has been
  keeping L4 for "moves Tiro would initiate on its own — of which there are
  none yet". Auto mode is that move. Setting `"/" = "L4"` in `trust.toml` is
  the user saying yes to it in advance, once per folder.
- The destination is **L3** and **already exists**. A new folder is a decision
  about the vault's shape; Tiro proposes it and waits.
- Triage returns `"obvious": true` with a `basis`: either a rule id from
  `rules.md`, or **at least two notes already in the destination folder** that
  are like this one. The runner checks that the rule exists, or that the notes
  exist and are in that folder. A basis that does not check out makes the
  proposal a proposal.
- The note is not a daily note (decision 6), has no `tiro:` verb, and is not in
  `Tiro/`.
- **No correction has ever been logged against this note.** A user who moved
  an auto-filed note back has answered; when they next edit it, Tiro may
  propose again but must not move it again. That is the difference between
  learning and insisting.

`tiro/filed-to` is a full path, so an obvious move may also give an `Untitled`
note a real name. That is within L4, but it is a rename, and the block says so.

**This departs from STATUS decision 8** ("`file` goes where the note says"). In
an auto move the destination is the skill's own key, and the checks above stand
in for the user's word. It is the one place the model's output picks a path,
which is why every part of the test is one the runner can check itself.
`Job` gains a `trigger` (`request` or `auto`), and the gate and `apply_output`
branch on it, not on the verb.

Anything short of that writes the proposal block and stops. A note that could
go two ways gets a `> [!question]`, as triage does now.

No constitution change is needed for any of this. `DESIGN.md` §1 ① ("move is
gated behind a one-word accept") and `safety.md`'s "A move is two permissions"
get a paragraph each saying that the accept can also be given in advance, per
folder, by raising it to L4.

### 3. Which notes: new or changed, settled, since the mode was switched on

Without bounds, the first run triages 929 notes, two thirds of them a dormant
Roam import, and spends the day's budget by 08:00.

- **Folders.** `[auto] folders`, by default the root plus every L4 folder
  (decision 7). A folder not listed is left alone. L0 and L1 folders are left alone
  whatever the list says.
- **New since the switch.** `since` is the time `tiro auto on` ran.
  A note untouched since then is never looked at. Nothing old gets swept.
- **Settled.** The existing 60 seconds is tuned for "tag and walk away". A
  capture written in bursts over half an hour would be triaged at every burst.
  `[auto] settle_minutes`, default 30: a note is looked at once it has been
  quiet that long.
- **Changed.** Looked at again only if its user content changed since, and at
  most once a day. A note already filed out of an auto folder is out of scope
  by definition, so filed notes are not re-triaged.
- **Explicit wins.** A note carrying `tiro:` follows the explicit protocol and
  auto mode ignores it.
- **Leave me alone.** `tiro: hold` on a note means auto mode never looks at it.
  It is a value of the user's key, not a job: the scan passes over it before a
  job exists, so it is never blocked as an unknown verb, and `lint` does not
  list it among the requests it could not read.
- **Order and budget.** Explicit requests run first. Auto jobs take what is
  left of the run, at most `[auto] max_per_run` (default 5), and count against
  the same daily ceilings.

### 4. Where "seen" lives: on the note, as now

Triage always writes a block, even if it only says "a fragment, nothing to
file". So every note auto mode looks at ends up with `tiro/hash` anyway, and
that is the record: no hash means new, a different hash means changed. No
second mechanism. The cost is that loose captures get Tiro's keys in their
frontmatter. That is what `tiro strip` is for.

Two things do live in `.tiro/state.json`, because they cannot live on the note:

- **`since`**, written by `tiro auto on`. Runtime state, not configuration.
- **Declined notes.** `tiro undo` on an auto job restores the note's bytes from
  before Tiro, with no hash. As it stands, the next run would see a new note and
  file it again, up to three times a day. So undoing an auto job records the
  note's user hash as declined, and the scan passes over a match. The user
  editing the note clears it.

### 5. Saying what was done unasked

The user's trust depends on being able to see what happened without asking.

- **The block** on a filed note opens with where it came from and how to undo
  it: *"Filed here from `/` on 2026-10-02, unasked, by R-014. `tiro undo
  <run>`, or move it back and Tiro will learn from that."*
- **The journal** gets its own section, *Done unasked*, above everything else.
  One line per note: what, where, why.
- **The commit** is `tiro(auto): <note>`, with the usual trailers plus
  `Tiro-Trigger: auto`.
- **Corrections.** A user moving an auto-filed note back is already a
  `moved-after-filing` correction. That is how auto mode learns: three of them
  and `reflect` proposes a rule. Nothing new to build, but auto triage must
  record its proposal in `state.json` the way explicit triage does.

### 6. Daily notes: yesterday's, once — **decided 2026-09-25**

Daily notes live at the root, and some captures happen inside them. Auto mode
looks at each one **once, the morning after its date**, for related notes and
suggestions only. A daily note is never moved and never renamed, whatever
triage says, and it is not looked at again when it changes later. Today's note
is never looked at: it is still being written.

### 7. Where the move is allowed: the root, plus folders the user names — **decided 2026-09-25**

Moves are in: the user asked for "when all seems clear, just do it". They may
start from the root (`"/" = "L4"`) and from any folder the user raises to L4 in
`trust.toml`. That list is the user's to write; `adopt` does not propose one.
Auto mode's `[auto] folders` defaults to exactly the L4 folders plus `"/"`, so
the two lists cannot drift apart unnoticed. A folder at L2 or L3 in
`[auto] folders` gets proposals only.

## Milestones

### M0 — The trigger (1 day)
`scan` gains the second clause: auto folders, `since`, settle time, seen
hashes, `tiro: hold`, declined notes, and at most once a day. `tiro status`
shows what auto mode *would* look at and why each of the rest was passed over.
`tiro auto on|off` records `since` in `state.json`.
- **Done when:** on a copy of the real vault, switching it on queues nothing;
  a new note at the root is queued 30 minutes after its last edit and not
  before; yesterday's daily note is queued once, and today's never; a note in
  a folder not listed is never queued; a note with `tiro:` is
  queued once, by the explicit rule, not twice.

### M1 — Auto triage, propose only (½ day)
The runner runs `triage` on auto jobs. The skill gains `obvious` and `basis` in
its answer, and the "suggested next verb" line.
- **Done when:** an auto-triaged note carries exactly the block explicit triage
  would have written, plus a line saying it was unasked, and is not looked at
  again until the user changes it.

### M2 — The obvious move (1½ days)
The move half of `apply_output` becomes a function both `file` and auto triage
call. The gate's rule 4 permits a move for an auto job when the source is L4,
the destination L3 and existing, and the basis checks out. One job, one commit,
one undo.
- **Done when:** a hostile test suite in the style of the gate's: an auto move
  from an L3 folder is refused; one into a new folder is refused; one with a
  basis naming notes that are not there is refused; one with a rule id that
  does not exist is refused; one for a note with a logged correction is
  refused. And one real capture at the root is filed into `uzh/` by analogy,
  `tiro undo` puts it back, and the next run leaves it there.

### M3 — Saying so (½ day)
The *Done unasked* journal section, the block header, the commit trailer, and
the proposal record for corrections. `lint`'s stale-inbox check becomes "auto
mode looked at this and did not act", with the reason.

### M4 — Doc amendments (½ day)
`rules/protocol.md` "The one rule" gets its second clause. `rules/safety.md`
gets L4's first use. `DESIGN.md` §1 ①, §4.2 and §11 are updated, and STATUS
records the decision and why.

### M5 — The week — **the user's**
Switch it on and live with it.

**Estimate: ~4 focused days**, plus the week.

## Acceptance criteria

1. **Nothing old is touched.** In the week, no note last modified before `since`
   gets a block or a move.
2. **Every unasked action is visible.** Every `tiro(auto)` commit has a line under
   *Done unasked* in that day's journal, and the other way round.
3. **Obvious means obvious.** Of the notes auto mode moved, the user moves back
   fewer than one in ten. More than that and the test in decision 2 is too
   loose; tighten it before widening anything.
4. **It is used.** At the end of the week, most new captures at the root have
   either been filed or carry a proposal, and the user has not had to remember
   a verb for them.

## Risks

| Risk | Mitigation |
|---|---|
| A capture is filed somewhere the user never looks again, and is lost in practice though not in git | Only into existing folders, with a basis the runner checks; the journal names every move; moving it back teaches `reflect` |
| Auto mode spends the budget on churn | `since`, settle time, once per note per day, `max_per_run`, and explicit requests go first |
| A note's text talks Tiro into a move | The runner chooses the verb; the move needs L4, L3 and a checked basis; the gate checks it all again afterwards |
| The user is mid-thought when Tiro moves the note away | 30 minutes of quiet first, and the 60-second guard and the mtime check before writing are still there |
| Blocks appear on notes the user considers finished and private | Folders not listed are left alone; `tiro: hold` per note; an L1 folder is never auto |
