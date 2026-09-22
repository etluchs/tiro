# Running Tiro on a timer

macOS uses **launchd**. The design docs say systemd, which is the Linux shape;
the loop and every safety property are identical either way.

## Before you turn it on

A timer removes the two things that made ad-hoc runs safe: someone watching the
terminal, and someone deciding when to run. So:

1. **Set the daily ceilings.** `[run] max_cost_usd_per_day` and
   `max_tokens_per_day` in `tiro.toml`. The per-run caps bound one pass;
   nothing bounds forty-eight passes without these. On a Claude subscription
   the SDK often reports no dollar figure, so the token ceiling is the one that
   always bites. `tiro status` prints today's spend against them.
2. **Run `tiro doctor`.** It is the only thing that tells you whether the
   Obsidian CLI actually answers queries, as opposed to merely reporting a
   version.
3. **Do one `file` job by hand first.** `obsidian move` is the one operation
   that can quietly maim the link graph, and it should not run unattended
   before it has run once with you watching.
4. **Leave `dispatch.live = false`** until the previews look right. It is the
   only job git cannot undo.

## Where the vault may live

**Not in `~/Documents`, iCloud Drive, or OneDrive.** Two independent reasons,
and the first one stops the timer dead:

- **macOS privacy control.** A launchd agent inherits none of the folder access
  your terminal was granted. A vault under `Documents` — which on a managed Mac
  is often a symlink into `~/Library/CloudStorage/...` — fails with
  `PermissionError: Operation not permitted` on the first file it touches, every
  half hour, forever. The alternative to moving it is granting Full Disk Access
  to the Python interpreter behind `.venv/bin/tiro`, which is a much larger
  permission than this job needs.
- **One syncer per vault** (DESIGN §6). A cloud client and git both managing the
  same tree is how `.git` gets corrupted and how files arrive dataless — present
  in a listing, unreadable on open. Tiro already rebases and pushes; it does not
  need a second opinion.

`~/vaults/<name>` or a sibling of this repo is the right shape. Move the folder,
reopen it in Obsidian, and point `vault` in `tiro.toml` at the new path.

## Install

```sh
sed -e "s|__REPO__|$PWD|g" -e "s|__HOME__|$HOME|g" \
    deploy/local.tiro.once.plist > ~/Library/LaunchAgents/local.tiro.once.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.tiro.once.plist
```

## Watch it

The schedule is hourly on the hour, 07:00 to 22:00, set with
`StartCalendarInterval`. An interval cannot express a window, and a vault does
not need attention at 04:00. A fire missed because the machine was asleep runs
once on wake, not once per hour missed.

```sh
launchctl print gui/$(id -u)/local.tiro.once | head -20   # is it loaded, when did it last run
tail -f ~/Library/Logs/tiro.log                           # what it printed
tiro status                                               # queue and today's spend
```

The log is the terminal you are not watching. The *journal*, in
`<vault>/Tiro/Journal/<date>.md`, is the supervision surface: every job, every
skip, every question, every failed push, each with the note linked. If a day is
not legible from the journal alone, that is a bug.

## Stop it

```sh
launchctl bootout gui/$(id -u)/local.tiro.once            # until you load it again
rm ~/Library/LaunchAgents/local.tiro.once.plist           # for good
```

To pause without unloading, set `max_tokens_per_day = 0` in `tiro.toml`: every
run then stops at the ceiling and says so in the journal.

## What it will and will not do

It reads the vault, acts on notes you tagged, writes inside its own blocks,
commits one job per commit, and pushes. It does **not** move anything unless a
note says `tiro: file`, which only you write, and it will not open Obsidian:
`auto` checks whether the app is already running before probing, because the
probe launches it.
