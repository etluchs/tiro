---
name: distill
description: Summarise a long note or a set of highlights, with links back.
---

# distill

Compress what is already here. This is not research: add nothing from outside
the vault.

- Lead with the single most important thing the source says.
- Keep the author's claims distinct from your reading of them.
- Link to the notes you drew on with `[[wikilinks]]`.
- If the material contradicts itself, that is the finding. Say it.
- If the note is too short to be worth distilling, say so and return
  `status: blocked` with that as the detail. Do not pad.

## The block

```markdown
> [!abstract] Tiro · distill · 2026-09-21
> The argument in one sentence.

- Point one, with the claim attributed.
- Point two.

**Drawn from** [[Source note]], [[Other note]]
```

## How to answer

End your reply with exactly one fenced `json` block. Nothing after it is read.

```json
{
  "status": "done | needs-input | blocked",
  "block": "the markdown to place in your block on the note",
  "keys": {},
  "detail": "one line for the journal, under 120 characters"
}
```

- `block` is markdown, opening with a `> [!abstract]` callout as above.
- `keys` may only contain keys starting with `tiro/`. Anything else is rejected.
- `needs-input` means you asked a question and are waiting. Prefer it to guessing.
- `blocked` means you could not do the job. Say why in `detail`.

You cannot write to the vault. You read; the runner writes what you return.
