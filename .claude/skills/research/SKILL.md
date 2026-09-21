---
name: research
description: Bounded, cited web research written into the note.
---

# research

Answer the question the note is asking. If it does not ask one outright, work
out what it is circling — and say what you took the question to be.

## Bounds

- Search sparingly. A handful of good sources, not a sweep.
- **Check the vault first.** If a note here already covers this, link it and
  build on it rather than starting from the web. A vault that compounds is the
  whole point.
- Every claim you did not bring with you carries a link and the date you read
  it. No source, no claim.
- Anything you could not verify goes in its own short **Unverified** list, not
  hedged through every sentence.
- If a source contradicts a note in this vault, say so plainly and link both.
  Never quietly pick a side.

## The block

```markdown
> [!abstract] Tiro · research · 2026-09-21
> The short answer, in one or two sentences.

Two or three paragraphs at most. Link to vault notes with [[wikilinks]].

**Sources**
- [Title](https://…) — read 2026-09-21

**Unverified**
- The 2024 figure appears only in a press release.
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
