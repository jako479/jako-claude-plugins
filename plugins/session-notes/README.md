# session-notes

Save a short note about a Claude Code session, list it in an index, then
archive the session.

## Use

```
/session-notes:archive-session
```

`/archive-session` also works when no other command has that name.

It writes:

```
<Notes folder>/<parent>/<project>/<session name>.md
<Notes folder>/<parent>/<project>/index.md
```

`<parent>/<project>` comes from the session's folder, for example
`PNFL/athc`. A session in a worktree uses its main folder. The newest session
is listed first in `index.md`.

Each note has:

- a one-line summary and a link that reopens the session
- Done, Decisions and Open, sized to the session
- Git: each branch the session used, whether it made it into main, and
  whether its worktree still exists
- links to design docs the session used
- `Follows:` links to earlier notes on the same branch or doc
- Dataview fields and `#tags` at the bottom

Archiving works in the desktop app only. In the CLI and VS Code it saves the
note and skips archiving.

If the Notes folder isn't found (for example Google Drive isn't running), it
asks you to retry, pick another folder, or cancel.

## Setup

Set **Notes folder** when you enable the plugin. Change it later in `/config`.

Needs Python 3 on the PATH.

## Install

```
/plugin marketplace add <owner>/jako-claude-plugins
/plugin install session-notes@jako-claude-plugins
```

## License

Apache-2.0. See [LICENSE](LICENSE).
