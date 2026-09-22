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

Rolling back restores tracked files and removes only paths Tiro itself created
this run. An untracked note may be one the user wrote and has not committed, and
git cannot tell us which — so rollback never deletes to tidy up after itself.

## Concurrency with a human

Obsidian is open while Tiro runs.

1. Skip notes modified in the last 60 seconds — the user is probably typing.
2. Re-`stat` before writing; if mtime moved, abandon and retry next run.
3. Write via temp file and atomic rename.
4. Tiro's own bookkeeping writes preserve mtime, so its housekeeping never looks
   like the user typing.

## Irreversible effects

`dispatch` is the only job git cannot undo. Its rules are in `DESIGN.md`
section 5.5 and are enforced by the runner, not by the agent: the agent drafts a
payload and never holds a tool that creates anything outside the vault.
