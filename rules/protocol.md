# The note protocol

Normative. The runner enforces this; skills must produce exactly this shape.
Full rationale in `docs/DESIGN.md` section 4.

## Frontmatter

Two keys the user writes, three Tiro writes back:

```yaml
---
tiro: research                    # the request — a verb, or a sentence (an order)
tiro/status: done                 # queued | working | done | blocked | needs-input
tiro/run: 2026-09-21T14-03Z-a4f2  # which run last touched this note
tiro/hash: 8f3c…                  # hash of the USER's content at that time
---
```

Verbs: `triage`, `file`, `research`, `distill`, `spec`, `dispatch`, `connect`. A
single other word is blocked with "unknown verb": it is almost always a typo.
One more word is not a verb: `tiro: hold` (or `#tiro/hold`) means "leave this
note alone". It is never queued, and auto mode never looks at the note.

**Orders.** Anything longer is an order, in the user's own words:
`tiro: translate this into German`. So is a `> [!tiro]` callout in the body,
which needs no frontmatter at all and reads like a note to a secretary:

```markdown
> [!tiro] Find the paper this argument comes from and link it.
```

The newest such callout is the current order, so a note can carry a
conversation: answer, follow-up, answer. Next to a verb, the callout refines
that job (`tiro: dispatch` with `> [!tiro] make it a Bug`) and cannot widen it.
An order runs as the job `order`: it writes one block on its own note and may
propose a destination in `tiro/filed-to`, and nothing else. Moving the note or
filing an issue still takes the verb, which the user writes. A body tag `#tiro/research` is read as
`tiro: research` anywhere in the note, so Obsidian's tag pane doubles as the
queue. The looser `#tiro research` that people actually type is read too, but
only at the end of a line, so that "#tiro file it tomorrow" stays a sentence.
A `#tiro` followed by anything else is not a request, and `lint` names it so
the user finds out.

**A note may ask for several things.** The `tiro:` value and every body tag
are all read, each once, and run in one pass, one job and one commit each, in
the order written, except that `file` always runs last because it moves the
note. `tiro/done` lists the requests already answered at the note's current
content, so a request that failed on the machine is retried alone and the
others are not run twice. A note from before `tiro/done` existed counts the
jobs named on its blocks.

Job-specific keys use the same namespace: `tiro/jira` holds a dispatched issue
key, `tiro/id` the note's stable uuid, `tiro/filed` where `file` put the note,
and `tiro/index` marks a folder index (its value is the folder). An index is
not a request: it carries no verb and is never queued; the runner rebuilds its
block, `index`, after the jobs of each run.

`tiro` itself — the verb — is the user's key. The runner refuses it from a
skill's output, so no job can queue the next one: `spec` cannot become
`dispatch` without the user writing the word.

## The one rule

> Act on a request when it is present **and** (`tiro/hash` is absent **or**
> `tiro/hash` differs from the hash of the note's current user content **or**
> the request is not in `tiro/done`).

**User content** is the note with Tiro's state keys and every Tiro-owned block
removed. Two keys stay in because the user edits them: the `tiro:` verb
(changing `triage` to `file` is the accept the filing flow waits for) and
`tiro/filed-to` (the destination the user accepted, which they may correct).
Tiro writes both before it records the hash, so its own output cannot trigger
Tiro, and the user's can. Get this wrong in either direction and either the
loop never terminates or the accept is never noticed.

## Auto mode: the second trigger

When auto mode is on (`tiro auto on`), a note with **no** `tiro:` is also acted
on when all of these hold:

- it is in an auto folder: `[auto] folders`, by default the root plus every L4
  folder, and that folder is at least L2;
- it was modified after auto mode was switched on;
- its hash is absent or differs, by the same rule as above;
- it has been quiet for `[auto] settle_minutes` (default 30), and was not
  looked at already today;
- it is not a daily note dated today. A daily note is looked at once, the
  morning after, and never again;
- the user did not undo an auto job on it at its current content.

The runner queues `triage` for it; the model never chooses the verb. The move
that may follow is the one rule in `safety.md` that uses L4. Requests the user
tagged run first, and auto jobs take at most `[auto] max_per_run` of what is
left. An auto job's commit is `tiro(auto): <note>` with `Tiro-Trigger: auto`,
and the journal lists it under **Done unasked**.

## Blocks

Write only between your markers. They are HTML comments: invisible in reading
view, greppable, stable.

```markdown
<!-- tiro:begin job=research id=a4f2 -->
> [!abstract] Tiro · research · 2026-09-21
> …

**Sources**
- [Title](https://…) — read 2026-09-21
<!-- tiro:end id=a4f2 -->
```

One block per job per note, keyed by `id`, which is `<tiro/id>-<job>`.
Re-running a job replaces its block in place: no duplicates, clean diffs, and
one job never overwrites another's. A block from before per-job ids is keyed
by the note's id alone, and its own job keeps replacing it there.

## Questions

```markdown
<!-- tiro:begin job=triage id=b91e -->
> [!question] Tiro asks
> This reads like it belongs in both `Projects/Hydra` and `Areas/Teaching`.
> Which? (answer below, then remove `tiro/status`)
>
> **Answer:**
<!-- tiro:end id=b91e -->
```

with `tiro/status: needs-input`. The user answering edits user content, so the
hash changes and the next run picks it up. Every open question is indexed in
`Tiro/Questions.md`.

## Status is ownership

While `tiro/status: working`, the note is Tiro's. In every other state it is the
user's, and Tiro touches only its own blocks and keys. A `working` note with no
live lock is a crashed run: reset it to `queued` and journal it.
