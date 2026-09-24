---
name: connect
description: Propose links from one note to notes it should know about, and name contradictions, each with quoted evidence.
---

# connect

The user has asked which notes in the vault this one should know about. Find
the few that matter, and any that **disagree** with it. You propose; the user
decides which links go into their text.

## What earns a suggestion

- A note that makes, extends, or depends on the same argument, not one that
  shares a word. "Both mention transformers" is not a reason; "this note's
  claim about scaling rests on the trend that note measures" is.
- **A contradiction is worth more than a link.** If another note says the
  opposite, or gives a different number for the same thing, name it. Never
  pick a side.
- Fewer is better. Five good links at most, often none. **"Nothing worth
  linking" is a good answer** and costs the user nothing.
- Skip notes this one already links to, and skip Tiro's own notes under `Tiro/`.

## Evidence, which the runner checks

Every suggestion carries two **verbatim** passages: one from this note (`here`)
and one from the target (`there`), each a sentence or so, 20 to 300 characters,
copied exactly from the user's text (not from a Tiro block). The runner looks
for both. A passage it cannot find drops the suggestion, and the block says how
many were dropped. Paraphrase is not evidence.

Name the target by its vault path without `.md`, e.g. `Areas/Compute trends`.

## How to answer

Search the vault with Grep and Glob, read the candidates, then end your reply
with exactly one fenced `json` block. Nothing after it is read.

```json
{
  "status": "done | needs-input | blocked",
  "payload": {
    "links": [
      {
        "target": "Areas/Compute trends",
        "why": "measures the trend this note's argument assumes",
        "here": "general methods that leverage computation are ultimately the most effective",
        "there": "training compute for frontier models doubled every six months"
      }
    ],
    "contradictions": [
      {
        "target": "Teaching/HS26/prog-2",
        "why": "says hand-built features still win on small data",
        "here": "…exact words from this note…",
        "there": "…exact words from that note…"
      }
    ]
  },
  "detail": "one line for the journal, under 120 characters"
}
```

- Leave `block` out; the runner writes the block from what it could check.
- `keys` must stay empty.
- `needs-input` means you asked a question and are waiting, with the question
  as a `> [!question]` callout in `block`.
- `blocked` means you could not do the job. Say why in `detail`.

You cannot write to the vault. You read; the runner writes what survives.
