"""Save a session note and add it to the project's index.

Used by the archive-session skill. Claude writes the text; this script finds
the session's name and start date, picks the folder and file name, and writes
the files. It prints one line of JSON.

    prepare  check the inputs and report where the note will go
    save     write the note and the index row from the draft
"""

# Original work by Brian Jacobs. Not derived from another plugin.

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
from datetime import date, datetime
from pathlib import Path

NOTE_MARKER = "===== NOTE ====="
MAX_NAME_LENGTH = 100
MAX_SUMMARY_LINES = 3
LEVELS = {"": 1, "1": 1, "2": 2}
SESSION_ID = re.compile(r"[0-9A-Za-z-]+")
# Characters Windows forbids in file names, plus ones that break Markdown links.
BAD_NAME_CHARACTERS = re.compile(r'[<>:"/\\|?*#%^\[\]{}()\x00-\x1f]')
RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", "INDEX"} | {
    f"{port}{n}" for port in ("COM", "LPT") for n in range(1, 10)
}
INDEX_HEADER = ["| Session | Created | Archived | Note |", "|---|---|---|---|"]


class NoteError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def parse_level(text: str) -> int:
    level = LEVELS.get(text.strip())
    if level is None:
        raise NoteError("bad_level", "The detail level must be 1 or 2.")
    return level


def notes_root(text: str) -> Path:
    text = text.strip()
    if not text or text.startswith("${"):
        raise NoteError("root_not_set", "The Notes folder setting is empty.")
    root = Path(text)
    if not root.is_dir():
        raise NoteError("root_missing", f"Notes folder not found: {root}")
    return root


def find_transcripts(projects_dir: Path, session_id: str) -> list[Path]:
    """The session's transcript files, oldest first."""
    return sorted(
        projects_dir.glob(f"*/{session_id}.jsonl"),
        key=lambda path: path.stat().st_mtime,
    )


def parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else None


def read_session(transcripts: list[Path]) -> tuple[str | None, str | None]:
    """The session's name and its local start date, from its transcripts."""
    custom = ai = None
    earliest = None
    for path in transcripts:
        with open(path, encoding="utf-8", errors="replace") as lines:
            for text in lines:
                try:
                    entry = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue
                if entry.get("type") == "custom-title" and entry.get("customTitle"):
                    custom = entry["customTitle"]
                elif entry.get("type") == "ai-title" and entry.get("aiTitle"):
                    ai = entry["aiTitle"]
                stamp = parse_timestamp(entry.get("timestamp"))
                if stamp and (earliest is None or stamp < earliest):
                    earliest = stamp
    created = earliest.astimezone().date().isoformat() if earliest else None
    return custom or ai, created


def main_folder(cwd: Path) -> Path:
    """The repository's main folder, also from a worktree or subfolder."""
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(cwd),
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return cwd.resolve()
    common = Path(result.stdout.strip())
    return common.parent.resolve() if common.name == ".git" else cwd.resolve()


def note_folder(root: Path, main: Path) -> tuple[Path, str]:
    """<root>/<parent>/<project>, and "parent/project" for display."""
    parts = [part for part in (main.parent.name, main.name) if part]
    return root.joinpath(*parts), "/".join(parts)


def file_stem(title: str) -> str:
    stem = " ".join(BAD_NAME_CHARACTERS.sub(" ", title).split())[
        :MAX_NAME_LENGTH
    ].rstrip(" .")
    if not stem:
        return "Untitled session"
    if stem.upper() in RESERVED_NAMES:
        return f"{stem} session"
    return stem


def session_marker(session_id: str) -> str:
    return f"<!-- session-id: {session_id} -->"


def choose_note_path(folder: Path, stem: str, session_id: str) -> Path:
    """A free file name, or this session's own note from an earlier save."""
    marker = session_marker(session_id)
    number = 1
    while True:
        path = folder / (f"{stem}.md" if number == 1 else f"{stem} {number}.md")
        if not path.exists() or marker in path.read_text(
            encoding="utf-8", errors="replace"
        ):
            return path
        number += 1


def read_draft(path: Path) -> tuple[list[str], str]:
    """The index summary lines and the note body."""
    if not path.is_file():
        raise NoteError("draft_missing", f"Draft not found: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    marker = next(
        (number for number, text in enumerate(lines) if text.strip() == NOTE_MARKER),
        len(lines),
    )
    summary = [text.strip() for text in lines[:marker] if text.strip()]
    body = "\n".join(lines[marker + 1 :]).strip()
    if not summary:
        raise NoteError("summary_missing", "The draft has no summary.")
    if len(summary) > MAX_SUMMARY_LINES:
        raise NoteError(
            "summary_too_long",
            f"The summary has {len(summary)} lines. The limit is {MAX_SUMMARY_LINES}.",
        )
    if not body:
        raise NoteError(
            "note_missing", f"The draft has no note after the {NOTE_MARKER} line."
        )
    return summary, body


def compose_note(
    title: str, project: str, created: str, archived: str, body: str, session_id: str
) -> str:
    return (
        f"# {title}\n\n"
        f"**Project:** {project}\n\n"
        f"**Created:** {created} · **Archived:** {archived}\n\n"
        f"{body}\n\n"
        f"{session_marker(session_id)}\n"
    )


def cell(text: str) -> str:
    return text.replace("|", "/").strip()


def update_index(
    index: Path,
    project: str,
    title: str,
    file_name: str,
    created: str,
    archived: str,
    summary: list[str],
) -> None:
    """Add the note's row, or replace it if the note is already listed."""
    link = file_name.replace(" ", "%20")
    link_text = cell(title).replace("[", "(").replace("]", ")")
    row = f"| [{link_text}]({link}) | {created} | {archived} | {'<br>'.join(cell(text) for text in summary)} |"
    if index.exists():
        lines = index.read_text(encoding="utf-8").splitlines()
    else:
        lines = [f"# {project} sessions", "", *INDEX_HEADER]
    key = f"]({link})"
    for number, text in enumerate(lines):
        if key in text:
            lines[number] = row
            break
    else:
        while lines and not lines[-1].strip():
            lines.pop()
        lines.append(row)
    write_text(index, "\n".join(lines) + "\n")


def write_text(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as file:
        file.write(text)


def draft_path(data_dir: str, session_id: str) -> Path:
    data_dir = data_dir.strip()
    if not data_dir or data_dir.startswith("${"):
        data_dir = str(Path(tempfile.gettempdir()) / "claude" / "session-notes")
    return Path(data_dir) / "drafts" / f"{session_id}.md"


def resolve(args: argparse.Namespace) -> dict:
    if not SESSION_ID.fullmatch(args.session_id):
        raise NoteError("session_missing", "The session ID is missing.")
    level = parse_level(args.level)
    root = notes_root(args.root)
    title, created = read_session(
        find_transcripts(Path(args.projects_dir), args.session_id)
    )
    folder, project = note_folder(root, main_folder(Path(args.cwd)))
    return {
        "level": level,
        "title": args.title.strip() or title,
        "created": created,
        "archived": date.today().isoformat(),
        "project": project,
        "folder": folder,
        "draft": draft_path(args.data_dir, args.session_id),
    }


def prepare(args: argparse.Namespace) -> dict:
    info = resolve(args)
    info["draft"].parent.mkdir(parents=True, exist_ok=True)
    info["draft"].unlink(missing_ok=True)
    note = (
        choose_note_path(info["folder"], file_stem(info["title"]), args.session_id)
        if info["title"]
        else None
    )
    return {
        "level": info["level"],
        "title": info["title"],
        "created": info["created"],
        "archived": info["archived"],
        "project": info["project"],
        "note_path": str(note) if note else None,
        "index_path": str(info["folder"] / "index.md"),
        "draft_path": str(info["draft"]),
    }


def save(args: argparse.Namespace) -> dict:
    info = resolve(args)
    if not info["title"]:
        raise NoteError(
            "title_missing", "The session has no name. Pass one with --title."
        )
    summary, body = read_draft(info["draft"])
    folder = info["folder"]
    folder.mkdir(parents=True, exist_ok=True)
    note = choose_note_path(folder, file_stem(info["title"]), args.session_id)
    created = info["created"] or "unknown"
    write_text(
        note,
        compose_note(
            info["title"],
            info["project"],
            created,
            info["archived"],
            body,
            args.session_id,
        ),
    )
    index = folder / "index.md"
    update_index(
        index, folder.name, info["title"], note.name, created, info["archived"], summary
    )
    info["draft"].unlink()
    return {"note_path": str(note), "index_path": str(index)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=["prepare", "save"])
    parser.add_argument("--root", required=True, help="the Notes folder setting")
    parser.add_argument("--session-id", required=True)
    parser.add_argument(
        "--data-dir", default="", help="folder for the draft (default: the temp folder)"
    )
    parser.add_argument("--level", default="", help="detail level: blank, 1 or 2")
    parser.add_argument(
        "--title", default="", help="session name to use when the session has none"
    )
    parser.add_argument(
        "--projects-dir", default=str(Path.home() / ".claude" / "projects")
    )
    parser.add_argument("--cwd", default=os.getcwd())
    args = parser.parse_args(argv)
    try:
        result = prepare(args) if args.command == "prepare" else save(args)
    except NoteError as error:
        print(json.dumps({"ok": False, "error": error.code, "message": str(error)}))
        return 1
    print(json.dumps({"ok": True, **result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
