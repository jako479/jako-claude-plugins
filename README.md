# jako-claude-plugins

My Claude Code plugins, and the marketplace that serves them.

## feature-dev-simple

A feature workflow that asks instead of guessing. One feature at a time.
Anything unclear becomes a question. Nothing gets built that isn't in the
approved spec.

Three agents of its own:

- `feature-dev-simple:code-explorer` — traces existing code, names the files
  worth reading
- `feature-dev-simple:code-architect` — designs to fit the codebase,
  recommends, never decides
- `feature-dev-simple:code-reviewer` — reports only what it is sure of

## Install

```
/plugin marketplace add <owner>/jako-claude-plugins
/plugin install feature-dev-simple@jako-claude-plugins
```

## Structure

```
.claude-plugin/marketplace.json   the catalog
plugins/<name>/                   one folder per plugin
  .claude-plugin/plugin.json      the only required file
  SKILL.md                        one skill lives here
  skills/<name>/SKILL.md          more than one goes here instead
  agents/*.md                     named <plugin>:<agent>
```

Plugin sources are relative to the repo root and cannot use `../`, so plugins
live inside this repo.

A published plugin `name` is fixed. Renaming breaks existing installs unless
`marketplace.json` gets a `renames` entry.

## Working on a plugin

Installing copies the plugin to `~/.claude/plugins/cache`, so edits here do
nothing until you reinstall. While changing one, load it from disk:

```
claude --plugin-dir ./plugins/<name>
```

It overrides the installed copy for that session. `/reload-plugins` picks up
later edits.

Before committing:

```
claude plugin validate .
claude plugin validate plugins/<name>
```

Every copied file says where it came from and that it changed.

## License

Apache-2.0. See [LICENSE](LICENSE).

The three agents are copies of Anthropic's
[feature-dev](https://github.com/anthropics/claude-plugins-official/tree/main/plugins/feature-dev),
also Apache-2.0. `SKILL.md` is my own.
