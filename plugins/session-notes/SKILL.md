---
name: archive-session
description: Save a short note about this session to the notes folder, list it in the project's index, then archive the session (desktop app only).
argument-hint: "[1|2]"
disable-model-invocation: true
allowed-tools: Bash(python *) PowerShell(python *)
---

<!-- Original work by Brian Jacobs. Not derived from another plugin. -->

# Archive session

Save a note about this session, list it in the project's index, then archive
the session.

The script prints one line of JSON. Run its commands exactly as written.

## 1. Prepare

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/session_notes.py" prepare --root "${user_config.notes_root}" --session-id "${CLAUDE_SESSION_ID}" --level "$ARGUMENTS"
```

If `ok` is false, act on `error`:

- `root_missing`: tell the user the notes folder wasn't found (Google Drive
  may not be running). Ask with AskUserQuestion: Retry or Cancel. The user
  can also type another folder.
  - Retry: run Prepare again.
  - Another folder: use `--root "<folder>"` instead, here and in step 3. Tell
    the user to change the Notes folder setting (`/config` in the CLI) to
    keep it.
  - Cancel: stop.
- `root_not_set`: tell the user to set the Notes folder setting (`/config` in
  the CLI), then stop.
- Anything else: tell the user the `message`, then stop.

If `title` is null, suggest a short name for the session and ask the user to
confirm it or type another. Pass it as `--title "<name>"` in step 3.

## 2. Write the draft

Write this to `draft_path`:

```
<1 to 3 short lines: what the session did, very high level>
===== NOTE =====
<the note>
```

The note, for `level` 1:

```
## Done
- <one line each>

## Decisions
- <one line each>

## Open
- <one line each>
```

Leave out empty sections. For `level` 2, add more bullets with key specifics:
files, commands, reasons. Don't add a title or dates; the script adds them.

Write very simple, clear, high-level text. Plain words. No fluff. Only facts
from this session.

## 3. Save

Run the Prepare command again with `save` in place of `prepare`, plus
`--title` or `--root` if step 1 set them.

- `summary_missing`, `summary_too_long` or `note_missing`: fix the draft and
  run Save again.
- Any other error: tell the user the `message` and stop. Don't archive.

## 4. Archive

Tell the user the `note_path`.

Then load `mcp__ccd_session_mgmt__archive_session` with ToolSearch. If it
loads (desktop app only), call it with `session_id: "self"` and
`reason: "Session notes saved"`. This ends the session.

If it doesn't load, tell the user archiving only works in the desktop app.
