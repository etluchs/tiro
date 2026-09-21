---
name: spec
description: Turn a note into a reviewable spec, for a human to sign off.
---

# spec

Turn what the note describes into something another agent could pick up and
execute. Then stop: **always return `status: needs-input`.** A spec is a draft
for the user to sign off, never a finished artefact — because the step after it,
`dispatch`, creates something no revert can take back.

## Sections, in this order

1. **Problem** — what is wrong or missing, in the user's terms. One paragraph.
2. **Context** — what an implementer needs that is not obvious. Link the vault
   notes that carry it.
3. **Acceptance criteria** — a numbered list, each one independently checkable.
   This is what makes the spec usable; spend your effort here.
4. **Non-goals** — what this explicitly does not cover.
5. **Open questions** — anything you had to assume. Be specific.

If the note is too vague to yield criteria anyone could check, say that under
**Open questions**, keep the spec short, and let the user fill the gap. A spec
full of invented requirements is worse than one with a hole in it.

## The block

```markdown
> [!abstract] Tiro · spec · 2026-09-21
> One sentence on the work. Review, then set `tiro: dispatch` to file it.

**Problem** …

**Context** …

**Acceptance criteria**
1. …

**Non-goals** …

**Open questions** …
```

## How to answer

End your reply with exactly one fenced `json` block. Nothing after it is read.

```json
{
  "status": "needs-input",
  "block": "the markdown to place in your block on the note",
  "keys": {},
  "detail": "one line for the journal, under 120 characters"
}
```

- `status` is always `needs-input` for this job — the user signs the spec.
- `keys` may only contain keys starting with `tiro/`. Anything else is rejected.
- `blocked` only if you could not produce a spec at all. Say why in `detail`.

You cannot write to the vault. You read; the runner writes what you return.
