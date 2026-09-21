# Prior art

Research notes behind [DESIGN.md](DESIGN.md). Surveyed September 2026. The short
version: the "agent in a markdown vault" space is crowded with *starter kits* and
thin plugins. Almost nobody has solved unattended operation — the interesting,
unclaimed ground is **a note protocol plus a safety gate that lets the agent run
without a human watching each tool call**.

## The landscape

| Project | What it is | What we take | What we leave |
|---|---|---|---|
| [Karpathy's LLM Wiki pattern](https://medium.com/@tahirbalarabe2/what-is-llm-wiki-pattern-persistent-knowledge-with-llm-wikis-3227f561abc1) (Apr 2026) | A spec, not code: drop sources in a folder, the LLM compiles them into a cross-referenced wiki; `CLAUDE.md` *is* the schema | The core insight — structure the vault so the model can *navigate* it, not just grep it. Answer from the wiki, not from a fresh web search. Note contradictions instead of silently overwriting | It assumes a greenfield wiki. Tiro must adopt a vault that already has years of the user's own structure |
| [AgriciDaniel/claude-obsidian](https://github.com/AgriciDaniel/claude-obsidian) | The most serious implementation of the above. 15 skills, inbox + content-addressed `.raw/`, source & claim ledgers, transaction engine | Preflight → hash-approve → atomic apply → rollback; conflict detection instead of silent overwrite; "switching filing modes does not silently reorganise old notes"; explicit network-egress consent | The bespoke transaction engine with SHA-256 approval plans. Git already gives us atomicity, audit and rollback for a fraction of the code |
| [ballred/obsidian-claude-pkm](https://github.com/ballred/obsidian-claude-pkm) | Goal-cascade productivity kit: 4 memory-carrying subagents, 10 slash commands, hooks | Auto-commit as a `PostToolUse` hook; a session-init hook that surfaces state; `/adopt`, which *detects* an existing PARA/Zettelkasten/LYT layout and maps folders interactively rather than imposing one | The whole 3-year-vision→daily-task cascade. That's a methodology, and the vault owner already has one |
| [MrLesk/Backlog.md](https://github.com/MrLesk/Backlog.md) | Git-native task manager; every task is a `.md` file with frontmatter, driven by humans and agents alike | "The same .md files humans read are the API surface for agents — no translation layer." Status-in-frontmatter as the whole state machine. This is exactly the dispatch target for Tiro's `spec` verb | Its CLI and board. Tiro's tasks live in the vault, not a `.backlog/` folder |
| [`acli`](https://developer.atlassian.com/cloud/acli/reference/commands/jira-workitem-create/) — Atlassian's official CLI | `jira workitem create --from-json`, `workitem search --jql`, `--json` output, `auth login` with a piped token | The posting mechanism for `dispatch`. It holds the credentials so we never do, and it is the same bargain we struck with the Obsidian CLI: delegate to whoever owns the semantics | Its `--from-json` schema (thin docs, weak `--generate-json`) — we pin and validate a small payload ourselves. And its exit codes, which are undocumented |
| [Atlassian MCP server](https://github.com/atlassian/atlassian-mcp-server) (official, hosted, OAuth 2.1 or API tokens) | Natural-language Jira/Confluence tools for AI clients | Worth having later for *reading* Jira | Not for creating issues: a model holding a create-issue tool is how you get ten of them, and Jira has no undo. The model drafts a payload, the runner posts it with `acli` (DESIGN §5.5). Also Cloud-only |
| Spec-driven development in [Jira](https://www.atlassian.com/software/jira/guides/agentic-engineering/spec-driven-development-jira) / [spec-kit](https://github.com/mbachorik/spec-kit-jira) | Specs live in the work item; a status transition ("Ready for Breakdown") triggers agent breakdown | **Status as an ownership signal, not a label**: `draft` = the human holds it, `approved` = handed to the agent, and agents only pick up approved items. This is the single best idea we found | Vendor coupling. Tiro produces a spec and stays transport-agnostic |
| [claude-code-ide](https://community.obsidian.md/plugins/claude-code-ide) and friends ([obsidian-claude-code-mcp](https://github.com/iansinnott/obsidian-claude-code-mcp), [Claude Code IDE Pro](https://community.obsidian.md/plugins/claude-code-ide-pro)) | An MCP/IDE bridge inside Obsidian: shares the active file, open files and selection over a local WebSocket | Useful for the *interactive* mode — Tiro sees what you're looking at | It is **read-only context, not a chat UI and not autonomy**. The README's "may be using sth like claude-code-ide" should not be mistaken for the user-interaction channel |
| [Obsidian CLI](https://obsidian.md/help/cli) (official, 1.12, Feb 2026) | Obsidian's own command line: file ops incl. `move`/`rename` **with link rewriting**, `unresolved`/`backlinks`/`orphans`/`deadends`, vault-wide `properties`, `search`, and `command <id>` to run any app or plugin command | All of it, as an optional backend — it owns link semantics and we should not reimplement them (DESIGN §3.4) | It as a *dependency*: it needs the running desktop app ([headless CLI closed as not planned](https://github.com/obsidianmd/obsidian-headless/issues/8)), its exit codes are always 0, and it does ~1 command per second |
| [Agentic Git Sync](https://community.obsidian.md/plugins/agentic-git-sync), [Direct Git Sync](https://community.obsidian.md/plugins/direct-git-sync) | Two-way vault↔GitHub sync, some with AI conflict resolution | Pull-before-write; keep the local file and write the remote side to a conflict copy rather than merging prose | AI auto-resolution of conflicts in the user's own prose. Tiro aborts instead |
| Community consensus on Claude + Obsidian ([xda](https://www.xda-developers.com/added-one-thing-to-claude-obsidian-setup-and-wikilinks-stopped-breaking/), [dsebastien](https://www.dsebastien.net/how-i-use-ai-with-my-obsidian-vault-every-day-16-practical-use-cases/)) | Practice reports | Scan for inbound wikilinks before moving anything; never rename a linked note without permission; keep the vault in git purely for reversibility; an agent at OS level bypasses Obsidian's rename-link-rewrite and silently breaks the graph | — |
| Headless-agent operations guidance ([1](https://dev.to/wartzarbee/how-to-run-claude-as-an-autonomous-agent-loops-memory-schedules-and-guardrails-jkj), [2](https://hidekazu-konishi.com/entry/claude_code_cicd_and_headless_automation.html)) | How people actually run unattended agents | Short scheduled runs beat one always-on process; read memory first, write memory last; idempotent + bounded + verifiable jobs; cap turns, time and cost; lock against overlapping runs; capture every run for audit | — |

## What nobody has done well

1. **A written contract between user and agent.** Every project above puts rules in
   `CLAUDE.md` and hopes. None defines a *protocol* — which frontmatter keys mean
   "do this", which mean "I did it", and how the agent knows a note is already
   handled. Without one there is no idempotency, so nothing can run unattended.
2. **A cheap safety gate.** claude-obsidian built a whole transaction engine;
   everyone else has nothing. The middle — validate the diff before committing,
   one commit per job — is unoccupied.
3. **Rules that actually evolve.** "The agent learns your conventions" is claimed
   everywhere and implemented nowhere beyond an append-only memory file. A
   correction log plus periodic, *proposed* rule changes is a small, honest
   mechanism that nobody ships.
4. **Handing work onward.** The vault agents stop at the vault; the spec-driven
   tools start at the issue tracker. The seam between "a thought in my notes" and
   "a task some other agent can execute" is Tiro's actual differentiator.
