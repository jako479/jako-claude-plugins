# jako-claude-plugins TODO

One line per task.

## TODO

- first commit
- delete `~/.claude/skills/dev-feature` and `dev-feature - Copy`
- push to GitHub, public
- add the marketplace, install `feature-dev-simple@jako-claude-plugins`
- disable `feature-dev@claude-plugins-official`
- run a real feature through it
- install `session-notes@jako-claude-plugins`, set its Notes folder, try it in
  the app, CLI and VS Code

## DECIDED FOR

- Own repo, not a fork
- Public, Apache-2.0 to match upstream
- Repo root is the marketplace; plugins in `plugins/<name>/`
- `SKILL.md` at the plugin root — one skill, no `skills/` folder
- `LICENSE` and `README.md` per plugin — installs copy only that folder
- Each copied file says its source and that it changed
- Opus. Effort `high` explorer, `xhigh` architect and reviewer
- Explorer and architect 1-2 with a test; reviewer always 1
- Names in the spec, checked by the reviewer
- `session-notes`: Claude writes the text, a Python script writes the files
- Notes in `<root>/<parent>/<project>/`, one `index.md` per project
- Session name from the transcript: renamed title, else the automatic one
- Detail levels 1 (default) and 2
- Index lists the newest session first

## DECIDED AGAINST

- Forking theirs — 39 plugins to get one folder
- Private repo — nothing here is private
- `claude-plugins` as marketplace name — validator rejects it
- `dev-feature` — too close to `feature-dev`
- `CONTRIBUTING.md`, `DEVELOPING.md` — README covers it
- Listing what changed in each file — goes stale
- 3 reviewers — same findings, 3x tokens
- A separate naming skill — reviewer already reads the diff
- A standalone skill for `session-notes` — only plugins get settings
- `<...>` links in the index — Obsidian's editor doesn't follow them
