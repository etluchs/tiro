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
  "detail": "one line for the journal, under 120 characters"
}
```

- `block` is markdown, opening with a `> [!abstract]` callout as above.
- `keys` may only contain keys starting with `tiro/`. Anything else is rejected.
- `obvious` and `basis` are optional; leave `obvious` false unless the section
  above applies.
- `needs-input` means you asked a question and are waiting. Prefer it to guessing.
- `blocked` means you could not do the job. Say why in `detail`.

You cannot write to the vault. You read; the runner writes what you return.
