# tiro
Tiro is an agent living in and with an obsidian vault, that is, a tree of md files.

## tasks
 - Organise content according to an evolving set of rules
 - sync the vault with a private git repo regularly
 - Act on appropriately tagged content, e.g.
    - research and reflect
    - turn into actionable items for fellow agents to work on, e.g, jira issues, git repos seeded with a spec, …
  
## structure

tiro 
 - runs inside an uzh agentic dev container, cr.gitlab.uzh.ch/zi-cloud-projekt/base-container-images/python-dev:latest
 - has the vault mounted
 - can talk to the user directly
     - via its console
     - may be using sth like https://community.obsidian.md/plugins/claude-code-ide
 - uses, i.e. reads and writes tags or page metadata to communicate with the user

This repo could have the vault repo as a git submodule. The less mutable of Tiro‘s instructions would live in this repo, others down in the vault. 
