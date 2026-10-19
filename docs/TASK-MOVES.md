# Task moves

Tasks get written down wherever the user happens to be, usually the day's
daily note, and they stay there. Triage could already tell where a task
belongs. On 2026-10-18 it said two of the four tasks in a daily note belong in
[[JupyterHub next]]. But it could only say so: moving the line was left to the
user. Now Tiro moves it, once the user has ticked a box, so each context's
backlog is complete without anyone copying lines by hand.

The request came in as a spec written in the vault. This is that spec's
answer, as built. Before this: [ITERATION-3](ITERATION-3.md).

## The shape, in one paragraph

Triage proposes and the user ticks, unless Tiro is sure. Then the runner
moves the task, and the model has no part in that step. For every open task in a note, triage names an
existing note it belongs in, or says that it stays. The runner checks each
destination and writes a `tasks` block on the note, with one checkbox per
move. Ticking a box accepts that move and no other. On the next run, the
runner moves each ticked task byte for byte. It goes from the source note to
just after the last task of the destination's first task list. A check that
does not depend on how the move was made then confirms that nothing else
changed on either side. A link to the destination stays where the task was.
The batch is one commit, so `tiro undo` or a `git revert` puts both notes
back exactly.

**When Tiro is sure, it moves right away** (added 2026-10-18, at the user's
request). Triage may say it is sure of a destination and give a basis the
runner can check. If the runner confirms it, and the note the task leaves is
in an L4 folder, the move is made in the same run, with no tick.

## The open questions, answered

### 1. Which rule changes, and how: (b), the runner moves

The **runner** carries out accepted moves as a mechanical operation, and code
checks the result. The agent is not given that job. This follows decision 1 in
STATUS ("the agent is read-only on the vault"). Under (a), the model would
have to remember an exception to never #3. Under (b), the model still has no
way to write, and the exception lives in code that can be tested:

- **Never #3** gains one sentence: when the user ticks a proposed task move,
  the runner moves that whole task, verbatim, from one note to another. It
  then checks that nothing else changed. This is the only exception, and it
  is not the agent's to use.
- **"One job, one note, one commit"** gains one sentence too: an accepted task
  move touches two notes, the one the task leaves and the one it joins, and is
  still one commit.

Both are in `CLAUDE.md`, and DESIGN §5.2 records them as well. The agent's tool
surface did not change.

The check is `tasks.check`. It compares the notes before and after, without
looking at how the move was made:

- the source's prose (Tiro's blocks and keys aside) may only have *lost*
  lines, and they must be exactly the moved ones;
- each destination may only have *gained* lines, and they must be exactly the
  ones moved into it, give or take a blank line around them.

The gate's usual rules apply on top: declared paths, no deletions, no broken
links, and trust. If any check fails, the move is undone from Tiro's own
record. The ticks are cleared and the reason goes on the note.

The proposal-only alternative (Tiro copies the line, the user deletes the
original) was not chosen. It leaves the same task in two places until the user
gets round to the second half, which is the half-done state never #4 is about.

### 2. Moves without a tick: yes, when Tiro is sure on a checkable basis

The first build left this out, as the spec's non-goals did. The user then
asked for it: "when Tiro is reasonably sure it should move right away, and
leave a link to the destination". It follows auto mode's pattern. The model
saying it is sure is needed and never enough; the runner confirms the basis
itself, and the consent is given per folder in advance.

The model gives `"sure": true` and one basis. The runner checks it against
the task and the destination as they are:

| Basis | What the runner checks |
|---|---|
| `{"tag": "jupyterhub"}` | the task carries `#jupyterhub`, and the destination uses it already, in its text or its frontmatter `tags` |
| `{"link": true}` | a link in the task resolves to the destination |
| `{"named": "JupyterHub"}` | the task's words (not its tags) say the name, and the destination's filename or an alias contains it; at least four characters |
| `{"rule": "R-012"}` | the rule exists in `.tiro/rules.md` |

On top of the basis:

- **The note the task leaves is in an L4 folder.** L4 is the level a user
  raises a folder to when Tiro may move things out of it unasked, for notes
  (auto mode) and now for tasks. Below L4, a sure move is a proposal, and the
  line under it says why.
- **Every check a ticked move gets**, at the moment it is made: the
  destination exists, is L3, is not a daily note or closed, has not changed,
  and the task is still exactly as written.
- **Never against the user.** `tiro undo` on an unasked move records that
  task and destination in `state.json` (`tasks.declined`). That move is then
  only ever proposed, never made unasked again.

A sure move is written into the block ticked by Tiro and made right after the
triage that proposed it, in the same run, as its own commit. The triage and
the move can then be undone apart. The commit carries `Tiro-Trigger: auto`,
and the journal lists it under **Done unasked**. The **Moved** record says
"unasked" and gives the basis.

### 3. Placement: after the first task list, as in criterion 5

After the last task of the first task list in the note, or at the end of the
note if it has none. `## Inbox` would add a heading the user did not write to
every destination that has no inbox, and a heading is a decision about the
note's shape. If that turns out to be wanted, it fits better as a vault rule
("tasks land under `## Inbox` where a note has one") than as a default.

At the end of the note means the end of the user's prose. If Tiro's own blocks
close the note, the task goes before them, so it is not stranded under a
callout.

### 4. Sources: any note triage looks at

Daily notes are the common case, and auto mode looks at yesterday's once, the
morning after. But nothing in the mechanics depends on the source being a
daily note, and a capture at the root holds tasks just as often. What the
source cannot be is a destination type: tasks never move *into* a daily note,
`Tiro/`, or a folder in `[tasks] never_into`.

## What the block looks like

```markdown
<!-- tiro:begin job=tasks id=3fa2c1-tasks -->
> [!abstract] Tiro · tasks · 2026-10-18
> 2 tasks belong in another note. Tick a move and Tiro makes it on its next run; nothing moves without a tick.

- [ ] Move to [[JupyterHub next]]: Fix the kernel spawner #jupyterhub <!-- tiro:move task=… to=Projects/JupyterHub%20next.md hash=… -->
- [ ] Move to [[JupyterHub next]]: Write the upgrade note <!-- tiro:move … -->

**Stays here**
- Call the dentist — a personal errand; no note holds those

**Moved**
- 2026-10-17: Draft the agenda → [[Teaching]]
<!-- tiro:end id=3fa2c1-tasks -->
```

- The text shown in a proposal has its Tasks-plugin fields cut. Otherwise the
  plugin would read Tiro's line as carrying the task's due date. The task that
  moves keeps all of them.
- The comment carries what the runner checks at the tick: the task's identity
  (a hash of every byte of it, sub-items included), the destination path, and
  the destination's hash when the move was proposed.
- The link is the user's to edit. Naming another note there turns the line
  into a new proposal for that note. That proposal is checked like any other
  and needs a tick of its own.
- A move Tiro was sure of and made carries `auto=<basis>` in its comment
  until it is made, and "— unasked, by #jupyterhub" in its record after.
- The block is its own, not part of triage's. A later triage replaces the
  proposals but keeps the **Moved** record and any move still ticked.

## What stays where the task was

A link to where it went, as a small block of Tiro's, in the task's place:

```markdown
- [ ] Call the dentist
<!-- tiro:begin job=moved id=3fa2c1-moved-9c1b7d4e -->
- ↗ moved to [[JupyterHub next]]: Fix the kernel spawner #jupyterhub
<!-- tiro:end id=3fa2c1-moved-9c1b7d4e -->
```

It is a block so that it is Tiro's text and not the user's. The check that the
source lost only the moved lines still holds, the user hash ignores it, and
the user may delete it freely. Tiro does not log that as a correction. It is
not a checkbox, so the Tasks plugin does not count it. The comment lines split
the list in two in reading view, which shows as a small gap. That is the cost
of the link being exactly where the task was.

## What the runner checks, and when

When a move is **proposed**, the destination must:

- be a markdown note that **exists**, inside the vault and not in a hidden
  folder;
- not be the note itself, a daily note (criterion 8), or one of Tiro's own;
- not be in a folder listed in `[tasks] never_into` (`Roam/` here, R-004);
- have a trust level of **L3**: putting a line into a note is putting
  something somewhere, so it needs what filing does.

A task the runner will not move gets a line under **Stays here** with the
reason, and the model is never asked about it. That covers a task inside a
quote or callout, one nested under another item, a numbered item, one with a
block id (`^abc`, which other notes may link to), and one followed directly
by unindented text that may or may not belong to it. Finished tasks
(`- [x]`) are never proposed (criterion 7).

When a move is **ticked**, all of the above again, and also:

- the task is still in the note exactly as proposed. Any edit makes it a
  different task, and it moves to **Not moved** with the reason;
- the destination has not changed since the proposal (criterion 9). If it
  has, the box is unticked, the hash is refreshed, and the line says so: tick
  again if the task still belongs there. Tiro's own earlier landings in the
  same note do not count as a change (`state.json`, `tasks.landed`). Without
  that, the second of two daily notes could never move a task into a note the
  first one had already moved a task into;
- the destination was not edited in the last minute, unless the last edit was
  Tiro's own landing. Otherwise the move waits, still ticked, for the next run;
- the source is a note Tiro may write at all. A tagged note counts its tag; an
  untagged one needs an L2 folder, as its block already did.

Every ticked move ends in one of three states. It is made, it is unticked
with the reason, or it is still ticked because it is waiting on a typing
guard. A tick is never left standing and silent. If it were, it would fire
every run (never #4).

## What does not trigger Tiro

The ticks are inside Tiro's block, which is outside the user hash, so a tick
is a trigger of its own. Carrying the move out clears the tick, so the trigger
fires once. The tasks leaving the source record the source's new hash, but
only if the hash was current before. A user edit the move did not cause still
counts as a change. Nothing is written to the destination except the task:
not a block, not a key. So `tasks.seen_but_landed` is how both scans, the
explicit one and auto mode's, tell Tiro's landing from the user's edit. A
destination that changed only by tasks Tiro moved into it is not looked at
again.

## The commit, the journal, undo

- One commit per batch: every ticked move from one source note in one run.
  The subject is `tiro(tasks): <note>`, with the trailers
  `Tiro-Run`, `Tiro-Job: tasks` and `Tiro-Note: <source>`. If either note
  had changes the user had not committed yet, they are committed first,
  as the user left them (`tiro: keep`). That is why reverting the commit
  restores both notes exactly (criterion 10).
- The journal entry names every move (criterion 11):
  `- ✓ tasks [[2026-10-17]] — moved 2 tasks`, followed by one line per task:
  `Fix the kernel spawner: [[2026-10-17]] → [[Projects/JupyterHub next]]`.
- `tiro undo <run>` reverts the commit. The revert brings the ticks back, and
  a tick is the trigger, so undo also unticks those moves. It says so under
  each one, and commits that without a `Tiro-Run` trailer. The undone move is
  not made again until the user ticks it again.
- `tiro status` lists notes with ticked moves waiting. Those moves run first
  in the next run, before any job. They use no model time.

## Known effects

- **A proposal is a checkbox, so the Tasks plugin sees it.** "Move to
  [[JupyterHub next]]: Fix the kernel spawner" shows up in [[Tasks]] under the
  daily note until it is ticked. That has an upside: a move can be accepted
  straight from the Tasks view. If it is noise instead, add a filter to the
  Tasks query: `description does not include tiro:move`. A move that has been
  made is a plain line, not a checkbox.
- **Moved tasks keep their links as written.** A link that resolves
  differently from the destination's folder, such as a relative markdown link
  or an ambiguous name, is caught by the gate's link check. The move is
  refused rather than made with a broken link.

## Acceptance criteria, and where each is tested

All in `tests/test_tasks.py`.

| # | Criterion | Test |
|---|---|---|
| 1 | a destination or a reason for every open task, destinations existing and linked | `test_triage_proposes_a_destination_or_a_reason_for_every_open_task`, `test_tasks_tiro_cannot_move_are_named_with_the_reason` |
| 2 | each move accepted on its own | `test_a_ticked_move_is_made_verbatim_and_only_that_one` |
| 3 | no accept, no change, byte for byte | `test_no_tick_means_no_move_ever` |
| 4 | arrives exactly as written, sub-items and Tasks fields included | `test_a_ticked_move_is_made_verbatim_and_only_that_one`, `test_the_check_refuses_anything_but_the_moved_lines` |
| 5 | after the last task of the first task list, or at the end | `test_a_task_lands_after_the_last_task_of_the_first_task_list`, `test_with_no_task_list_a_task_lands_at_the_end_before_tiros_blocks` |
| 6 | the source's block records each move with a link | `test_a_ticked_move_is_made_verbatim_and_only_that_one` |
| 7 | finished tasks never proposed | `test_triage_proposes_a_destination_or_a_reason_for_every_open_task` |
| 8 | never into `Roam/` or a daily note | `test_a_destination_that_fails_a_check_is_never_proposed` |
| 9 | a changed destination: not moved, said, proposed again | `test_a_destination_changed_since_the_proposal_is_proposed_again` |
| 10 | one commit; reverting it restores both | `test_a_batch_is_one_commit_and_reverting_it_restores_both_notes`, `test_undo_takes_the_move_back_and_unticks_it` |
| 11 | the journal lists every move | `test_the_journal_lists_every_move_with_source_and_destination` |
| 12 | no fitting destination: stays, and is named | `test_triage_proposes_a_destination_or_a_reason_for_every_open_task`, `test_a_destination_that_fails_a_check_is_never_proposed` |
| — | sure: moved in the same run, a link left behind, listed as unasked | `test_a_sure_move_is_made_in_the_same_run_and_leaves_a_link` |
| — | sure is not enough without L4, or with a basis that does not hold | `test_sure_is_not_enough_outside_an_l4_folder`, `test_the_runner_checks_the_basis_itself`, `test_a_task_that_names_or_links_its_note_is_sure` |
| — | an undone unasked move is never made unasked again | `test_an_undone_unasked_move_is_never_made_unasked_again` |
| — | a ticked move leaves a link too | `test_a_ticked_move_leaves_a_link_where_the_task_was` |

## What is left for the user

1. Add `[tasks] never_into = ["Roam/"]` to `tiro.toml`. Criterion 8 depends on
   it as much as on `trust.toml`: if `Roam/` is below L3 there, it is closed
   already, but the list says so whatever the level.
2. Make sure the folders tasks should land in are **L3** in
   `.tiro/trust.toml`. A folder below L3 gets no proposals, only a line saying
   why.
3. For moves without a tick, raise the folder the daily notes live in to
   **L4** (`"/"` or `"journal/"`). That also lets auto mode file notes out of
   it, and daily notes are never filed, so for a daily-notes folder L4 means
   exactly "move tasks out unasked". Leave it below L4 and every move waits
   for a tick.
4. Let auto mode triage a few daily notes, tick some moves, and look at
   [[JupyterHub next]] afterwards. None of this has run against the real vault
   yet.
