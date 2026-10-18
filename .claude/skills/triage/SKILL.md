---
name: triage
description: Classify an inbox note and propose where it belongs. Moves nothing.
---

# triage

Read the note and propose how it should be filed. **You propose; you do not move
anything.** The user accepts by setting `tiro: file`, and a separate job does the
move.

Sometimes nobody asked: auto mode runs triage on notes the user wrote without
tagging them, and the job says so. Everything below applies unchanged. The one
difference is that the runner may do the move itself when you say the
destination is **obvious** and give a basis it can check (see below).

Read `.tiro/rules.md` first, if the vault has one. It is this vault's filing
conventions, and it outranks your instincts about how notes "should" be
organised. Where the rules are silent, or there are none yet, go by what the
vault already does: find where notes like this one live and propose by analogy,
and say in the block that the proposal is inferred rather than rule-backed.
Expect the vault to be inconsistently organised, or not organised at all. That
is what you are here for. Do not treat it as a reason to stop.

## What to work out

1. **A title**, if the note has none or has a placeholder like "Untitled" or a
   bare date stamp.
2. **Tags** that match tags already in use in this vault. Look at what exists
   before you invent one; a tag used once is worse than no tag.
3. **Links** to notes that already exist and are genuinely related. Search the
   vault. Two good links beat eight plausible ones.
4. **A destination**, justified by a rule from `rules.md` where one applies. Put
   it in `keys` as `tiro/filed-to`, as a full vault-relative path including the
   filename: `Areas/Didaktik/spaced-repetition.md`. A folder that does not
   exist yet is a fine destination when nothing existing fits; say that it is
   new. Never propose moving a daily note (`YYYY-MM-DD`): those stay where the
   daily-notes plugin put them.

5. **A next step**, if the note plainly wants one: a question to look up
   (`research`), a long text to summarise (`distill`), an idea to turn into a
   spec (`spec`). Name the verb in one line, e.g. "Reads like a question to
   look up: add `tiro: research`." You do not start it. Most notes need none.

## Open tasks

When the note has open tasks (`- [ ]`), the job lists them for you, numbered.
Tasks get written down wherever the user happens to be, usually the day's
daily note; your job is to say where each one belongs, so that each context's
backlog is complete.

For each, either name the **existing note** it belongs in, or say it stays and
why. Look at where tasks like it already live: a note with a task list for that
project, that person, that context. Follow `.tiro/rules.md` (folders are
contexts; daily notes and archives are never destinations). A task stays when
nothing fits, when it is about the day itself, or when you cannot tell. Never
propose a new note to hold one task; if one would help, say so in `why`.

You only name. The runner checks each destination, writes the proposals as a
checkbox per move, and moves nothing until the user ticks one. It moves the
task exactly as written, so do not suggest rewording.

## Obvious, and why

Say `"obvious": true` only when the destination is clearly right: a rule in
`rules.md` covers the note, or the destination folder already holds notes that
are plainly of the same kind. Give the basis:

- `{"rule": "R-014"}` for a rule, by its id; or
- `{"like": ["uzh/Meeting 2026-09-01.md", "uzh/Budget HS26.md"]}` for at least
  two existing notes in the destination folder that this one is like.

The runner checks the basis before anything moves, and a basis that does not
hold up turns the move back into a proposal. Never say obvious for a new
folder, a daily note, or a note that could reasonably go two ways. When in
doubt, it is not obvious: a proposal costs the user one word.

## Leftovers

An empty note, a file still called "Untitled", a fragment too short to mean
anything: say so in one line and propose `Archive/` as the destination. Never
propose deleting anything. Tiro cannot, and the user decides what is junk.

## When to ask instead

If the note plausibly belongs in two places and the choice matters, write a
`> [!question]` callout naming the specific choice and return
`status: needs-input`. But a proposal costs the user a glance and a question
costs them a decision, so when the vault gives you anything to go on, propose.

## The block

```markdown
> [!abstract] Tiro · triage · 2026-09-21
> A one-line statement of what this note is.

**Proposed** `Areas/Didaktik/spaced-repetition.md` — R-014 (course material).
**Tags** #didaktik #lernen
**Related** [[Spacing effect]], [[HS26 Vorlesung 3]]
```

## How to answer

End your reply with exactly one fenced `json` block. Nothing after it is read.

```json
{
  "status": "done | needs-input | blocked",
  "block": "the markdown to place in your block on the note",
  "keys": {"tiro/filed-to": "Areas/…/name.md"},
  "obvious": false,
  "basis": {"rule": "R-014"},
  "tasks": [
    {"n": 1, "to": "Projects/JupyterHub next.md", "why": "the hub upgrade backlog"},
    {"n": 2, "to": "", "why": "a personal errand; no note holds those"}
  ],
  "detail": "one line for the journal, under 120 characters"
}
```

- `block` is markdown, opening with a `> [!abstract]` callout as above.
- `keys` may only contain keys starting with `tiro/`. Anything else is rejected.
- `obvious` and `basis` are optional; leave `obvious` false unless the section
  above applies.
- `tasks` has one entry per open task the job listed, by its number. `to` is
  a vault-relative path to an existing note, or empty for a task that stays.
  `why` is a few words. Leave `tasks` out when the job lists none.
- `needs-input` means you asked a question and are waiting. Prefer it to guessing.
- `blocked` means you could not do the job. Say why in `detail`.

You cannot write to the vault. You read; the runner writes what you return.
