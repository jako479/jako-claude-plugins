# session-notes

Save a short note about a Claude Code session, list it in an index, then
archive the session.

## Use

```
/session-notes:archive-session      short note
/session-notes:archive-session 2    more detail
```

It writes:

```
<Notes folder>/<parent>/<project>/<session name>.md
<Notes folder>/<parent>/<project>/index.md
```

`<parent>/<project>` comes from the session's folder, for example
`PNFL/athc`. A session in a worktree uses its main folder.

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
