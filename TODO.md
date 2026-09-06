# jako-claude-plugins TODO

One line per task.

## TODO

- first commit
- delete `~/.claude/skills/dev-feature` and `dev-feature - Copy`
- push to GitHub, public
- add the marketplace, install `feature-dev-simple@jako-claude-plugins`
- disable `feature-dev@claude-plugins-official`
- run a real feature through it

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

## DECIDED AGAINST

- Forking theirs — 39 plugins to get one folder
- Private repo — nothing here is private
- `claude-plugins` as marketplace name — validator rejects it
- `dev-feature` — too close to `feature-dev`
- `CONTRIBUTING.md`, `DEVELOPING.md` — README covers it
- Listing what changed in each file — goes stale
- 3 reviewers — same findings, 3x tokens
- A separate naming skill — reviewer already reads the diff
