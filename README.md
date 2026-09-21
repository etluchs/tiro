# tiro

Tiro is an agent living in and with an obsidian vault, that is, a tree of md files.

> Marcus Tullius Tiro was Cicero's secretary. He took dictation, kept the
> archive, and invented a shorthand to keep up. He did not write the speeches.

Tiro files, drafts, researches and prepares. The user signs.

## tasks
 - Organise content according to an evolving set of rules
 - sync the vault with a private git repo regularly
 - Act on appropriately tagged content, e.g.
    - research and reflect
    - turn into actionable items for fellow agents to work on, e.g, jira issues, git repos seeded with a spec, …

## how it works

The user tags a note. Tiro picks it up on its next run, does the work inside a
block it owns, and either finishes or asks a question in the note itself. Every
job is one git commit, so everything is attributable and one command from undone.

```yaml
---
tiro: research           # the request
tiro/status: done        # the reply
---
```

## structure

tiro
 - runs inside an uzh agentic dev container, cr.gitlab.uzh.ch/zi-cloud-projekt/base-container-images/python-dev:latest
 - has the vault mounted, as a sibling git repo (not a submodule — see the design doc)
 - reads and writes frontmatter to communicate with the user, asynchronously, in the notes themselves
 - can also talk to the user directly via its console (`tiro chat`)

Tiro's less mutable instructions — the constitution, the safety rules, the
protocol — live in this repo. The vault's own filing rules live down in the
vault, in `.tiro/`, where Tiro may propose changes but never make them.

## docs

- **[Design](docs/DESIGN.md)** — the verdict on the sketch, the note protocol, the
  safety model, the git strategy, how rules evolve
- **[Iteration 1](docs/ITERATION-1.md)** — what to build first, in what order, and
  how we know it works
- **[Prior art](docs/PRIOR-ART.md)** — what the neighbours built and what we took
