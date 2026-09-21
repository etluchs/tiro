# The note protocol

Normative. The runner enforces this; skills must produce exactly this shape.
Full rationale in `docs/DESIGN.md` section 4.

## Frontmatter

Two keys the user writes, three Tiro writes back:

```yaml
---
tiro: research                    # the request — a verb from the closed set
tiro/status: done                 # queued | working | done | blocked | needs-input
tiro/run: 2026-09-21T14-03Z-a4f2  # which run last touched this note
tiro/hash: 8f3c…                  # hash of the USER's content at that time
---
```

Verbs: `triage`, `file`, `research`, `distill`, `spec`, `dispatch`. Anything else
is blocked with "unknown verb". A body tag `#tiro/research` is read as
`tiro: research`, so Obsidian's tag pane doubles as the queue.

Job-specific keys use the same namespace: `tiro/jira` holds a dispatched issue
key, `tiro/id` the note's stable uuid.

## The one rule

> Act on a note when `tiro:` is present **and** (`tiro/hash` is absent **or**
> `tiro/hash` differs from the hash of the note's current user content).

**User content** is the note with every `tiro/*` frontmatter key and every
Tiro-owned block removed. Tiro's own output therefore cannot trigger Tiro. Get
this wrong and the loop never terminates.

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

One block per job per note, keyed by `id`. Re-running replaces the block in
place: no duplicates, clean diffs.

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
