---
name: archive-session
description: Save a short note about this session to the notes folder, list it in the project's index, then archive the session (desktop app only).
disable-model-invocation: true
allowed-tools: Bash(python *) PowerShell(python *)
---

<!-- Original work by Brian Jacobs. Not derived from another plugin. -->

# Archive session

Save a note about this session, list it in the project's index, then archive
the session.

Run the script's commands with the Bash tool, exactly as written. It prints
one line of JSON.

## 1. Prepare

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/session_notes.py" prepare --root "${user_config.notes_root}" --session-id "${CLAUDE_SESSION_ID}"
```

If `ok` is false, act on `error`:

- `root_missing`: tell the user the notes folder wasn't found (Google Drive
  may not be running). Ask with AskUserQuestion: Retry or Cancel. The user
  can also type another folder.
  - Retry: run Prepare again.
  - Another folder: use `--root "<folder>"` instead, here and in step 4. Tell
    the user to change the Notes folder setting (`/config` in the CLI) to
    keep it.
  - Cancel: stop.
- `root_not_set`: tell the user to set the Notes folder setting (`/config` in
  the CLI), then stop.
- Anything else: tell the user the `message`, then stop.

If `title` is null, suggest a short name for the session and ask the user to
confirm it or type another. Pass it as `--title "<name>"` in step 4.

## 2. Get the session link

Load `mcp__ccd_session_mgmt__get_session` with ToolSearch. If it loads
(desktop app only), call it with `session_id: "self"` and pass its `link` as
`--link "<link>"` in step 4. Otherwise skip this step.

## 3. Write the draft

Write this to `draft_path`:

```
summary: <project>: <what the session did, one short line>
tags: <2 to 5 topic tags, comma-separated>
===== NOTE =====
## Done
- <one line each>

## Decisions
- <one line each>

## Open
- <one line each>
```

- `summary`: very high level, like `athc: Added gameplan check`.
- `tags`: topics such as component, subcommand, logging, cli,
  ai-configuration. Reuse `known_tags` where they fit. Lowercase, hyphens
  instead of spaces.
- Leave out empty sections. Size the note to the session: a short session
  gets a few bullets, a long one gets more.
- Very simple, clear, high-level text. Plain words. No fluff. Only facts from
  this session.
- Don't add a title, dates, git status, links or tags to the body. The script
  adds them.

## 4. Save

Run the Prepare command again with `save` in place of `prepare`, plus
`--link`, `--title` or `--root` if the steps above set them.

- `summary_missing`, `draft_bad_header` or `note_missing`: fix the draft and
  run Save again.
- Any other error: tell the user the `message` and stop. Don't archive.

## 5. Archive

Tell the user the `note_path`.

Then load `mcp__ccd_session_mgmt__archive_session` with ToolSearch. If it
loads (desktop app only), call it with `session_id: "self"` and
`reason: "Session notes saved"`. This ends the session.

If it doesn't load, tell the user archiving only works in the desktop app.
