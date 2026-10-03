# Safety

Full rationale in `docs/DESIGN.md` section 5.

## The trust ladder

Per folder, from `<vault>/.tiro/trust.toml`. The most specific folder wins.
The key `"/"` is the vault root itself — files directly in it, not everything
below — for vaults that keep their loose notes at the top level.

| Level | Tiro may |
|---|---|
| L0 | read and report only |
| L1 | + append inside its own blocks |
| L2 | + write `tiro/*` frontmatter keys |
| L3 | + create new notes |
| L4 | + move and rename, rewriting inbound links |
| L5 | delete — **not implemented, and refused by name** |

Enforced twice: in the permission callback before a tool runs, and in the gate
after the job, against the actual working tree. The first is a policy decision;
the second is a fact.

## The tag is the consent

A note carrying `tiro:` is treated as L2 *for itself*, whatever its folder's
level — the user asked, on the note, and that is the most explicit consent
there is. The one exception is L0: a folder at L0 is one Tiro does not touch,
and a tag inside it is a note, not a request. The scan skips it and the journal
says why; the note itself is not written, not even to mark it blocked.

Without this rule, a vault with no folder structure would need every folder
listed before a single request could run.

## An order is the user's words, and no wider than a block

A free-form order (`tiro: <sentence>`, or a `> [!tiro]` callout) is the one
place note text is an instruction. What it can do is fixed in code, not by the
words: one block on its own note, and at most a proposed `tiro/filed-to`. Any
other key is dropped and the journal says so — an order that could write
`tiro/jira` could make `dispatch` believe an issue exists. The agent is
read-only whatever the order says, and the gate judges the job like any other.

The residual risk is a pasted page that happens to contain a `> [!tiro]`
callout. It gets a block on that one note and the read-only tools, the web
ones included — which is no more than a page pasted into a `research` note can
already steer. The journal names every order that ran, and `tiro status` shows
the words before they run.

## A move is two permissions

Taking a note *out of* where the user put it is the risky half — that is what
breaks the graph and loses things. But every move Tiro makes is one the user
asked for by writing `tiro: file`, and the constitution's rule is "never
outside an L4 folder *without an explicit accept*". The tag is that accept, so
the **source** need only be somewhere Tiro may act at all: not L0. L4 keeps its
meaning for moves Tiro would initiate on its own — of which there are none yet.

Putting a note somewhere is ordinary creation, so the **destination** must be
L3. Filing *into* an area is opt-in per folder: an area below L3 is one the user
has not opened up, and `file` blocks with a message naming the folder and its
level. A destination folder that does not exist yet is created — a vault
without structure grows its folders one filing at a time.

The permissive shape, and the one `tiro adopt` drafts, is `default = "L3"` with
the few folders that are private or archival pinned to L0 or L1.

## Auto mode is the move L4 was kept for

Auto mode (`protocol.md`) moves notes nobody tagged. With no tag there is no
accept on the note, so never #2 applies in full: the source folder must be
**L4**. Raising a folder to L4 is the user's accept, given in advance. On top of
that, the runner moves a note only when all of these hold, and says on the
note which one failed otherwise:

- triage said the destination is obvious and gave a basis, and the basis
  checks out: a rule id that is in `rules.md`, or at least two existing notes
  in the destination folder;
- the destination is L3 and its folder **already exists**. Auto mode never
  creates a folder;
- the note is not a daily note, has never been filed by Tiro, and the user has
  never corrected Tiro on it or undone an auto job on it.

So Tiro moves a note at most once, and never against the user. The gate checks
the source's level and the folder rule again afterwards, as a fact.

Without the tag, "L2 for itself" does not apply: an auto job may write its
block only where the folder is L2 or higher.

## The gate

Before any commit, every one of these must hold, or the job's changes are rolled
back and the note is marked `blocked`:

1. Frontmatter still parses and conforms to the protocol.
2. No file outside the job's declared paths changed.
3. No file deleted.
4. No file moved or renamed, unless the job is `file` and the trust levels above
   permit that particular move.
5. Every wikilink that resolved before the job still resolves.
6. The note's mtime is unchanged since the job read it.

Before a job writes to a note the user has changed since the last commit, the
runner commits it as they left it (`tiro: keep …`, with no `Tiro-Run`
trailer). The job's own commit is then only Tiro's change, and `tiro undo`,
a `git revert` of the run's job commits, takes away Tiro's work and not the
user's. Without it, undoing a job on a note that was never committed deleted
the note. The journal commit is not reverted: it is the record.

Undoing a job puts back what Tiro changed, and nothing else. It does not use
`git checkout`: HEAD lacks every edit the user has not committed, so restoring
it reverts the user along with Tiro. Each job records the bytes of every file
before and after Tiro writes it, and undo restores a file only if it is still
exactly as Tiro left it. A file that has changed since — the user saving, or
Obsidian doing something nobody predicted — is named in the note and left as
found. A file is removed only if the record shows Tiro created it and nobody has
touched it since.

## Concurrency with a human

Obsidian is open while Tiro runs.

1. Skip notes modified in the last 60 seconds — the user is probably typing.
2. Re-`stat` before writing; if mtime moved, abandon and retry next run.
3. Write via temp file and atomic rename.
4. Tiro's own bookkeeping writes preserve mtime, so its housekeeping never looks
   like the user typing.
5. The gate's "before" is taken after the agent returns, not when the job
   starts. The agent cannot write, so whatever changes while it thinks is the
   user's, and is neither blamed on the job nor undone.

## Irreversible effects

`dispatch` is the only job git cannot undo. Its rules are in `DESIGN.md`
section 5.5 and are enforced by the runner, not by the agent: the agent drafts a
payload and never holds a tool that creates anything outside the vault.
