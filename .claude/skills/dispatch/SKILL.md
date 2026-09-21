---
name: dispatch
description: Draft the Jira payload for a signed-off spec. Creates nothing.
---

# dispatch

The user has signed off a spec by setting `tiro: dispatch`. Draft the Jira work
item. **You are drafting a payload, not creating an issue** — you hold no tool
that can reach Jira, and the runner posts what you return, after its own checks.

- **Summary**: what to *do*, not what the note is about. "Add rate limiting to
  the ingest endpoint", not "Rate limiting". Under 120 characters, imperative,
  no ticket-speak.
- **Description**: the spec's problem, acceptance criteria and non-goals as
  plain text — Jira Cloud does the formatting. Keep the criteria numbered: they
  are what closes the issue. Name the source note so a reader can find it.
- **Type**: from the vault rules if they say, otherwise leave it out and the
  configured default is used. **Never set a project key** — a payload naming one
  is rejected.
- **Labels**: the runner adds `tiro` and the note's id label itself. Add others
  only if the vault rules ask for them.

If the spec still has unanswered open questions, return `status: needs-input`
and name them. An issue built on an assumption is how a tracker fills with work
nobody wants.

## The block

```markdown
> [!abstract] Tiro · dispatch · 2026-09-21
> Ready to file as a Task. Nothing has been created yet.
```

## How to answer

End your reply with exactly one fenced `json` block. Nothing after it is read.

```json
{
  "status": "done | needs-input | blocked",
  "block": "the markdown to place in your block on the note",
  "payload": {
    "summary": "Add rate limiting to the ingest endpoint",
    "description": "Problem…\n\nAcceptance criteria\n1. …\n\nNon-goals…\n\nSource: [[note name]]",
    "type": "Task",
    "labels": []
  },
  "detail": "one line for the journal, under 120 characters"
}
```

- `needs-input` means you asked a question and are waiting. Prefer it to guessing.
- `blocked` means you could not draft a payload. Say why in `detail`.

You cannot write to the vault, and you cannot reach Jira. You read; the runner
writes what you return and decides whether anything is posted.
