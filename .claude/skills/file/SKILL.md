---
name: file
description: Apply an accepted triage proposal. The runner moves the note.
---

# file

The user has accepted a triage proposal by setting `tiro: file`. Confirm the
destination and hand it back. The runner does the move through the Obsidian CLI,
so that Obsidian rewrites every inbound link itself.

1. Find the destination. Normally it is already on the note as `tiro/filed-to`,
   written by `triage`. If the user edited it, theirs wins.
2. Sanity-check it: does the folder exist in this vault, does the filename keep
   the note's identity, does it agree with `.tiro/rules.md`?
3. Return it as `keys: {"tiro/filed-to": "<vault-relative path>.md"}`.

If there is no destination on the note, or it names a folder that does not
exist, return `status: needs-input` with a question. Do not invent one: this is
the only job that changes where things live.

## The block

```markdown
> [!abstract] Tiro · file · 2026-09-21
> Filed to `Areas/Didaktik/spaced-repetition.md`.
```

## How to answer

End your reply with exactly one fenced `json` block. Nothing after it is read.

```json
{
  "status": "done | needs-input | blocked",
  "block": "the markdown to place in your block on the note",
  "keys": {"tiro/filed-to": "Areas/…/name.md"},
  "detail": "one line for the journal, under 120 characters"
}
```

- `keys` may only contain keys starting with `tiro/`. Anything else is rejected.
- `needs-input` means you asked a question and are waiting. Prefer it to guessing.
- `blocked` means you could not do the job. Say why in `detail`.

You cannot write to the vault. You read; the runner writes what you return.
