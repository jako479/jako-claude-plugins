"""Save a session note and add it to the project's index.

Used by the archive-session skill. Claude writes the text; this script finds
the session's name, dates, git status and design docs, picks the folder and
file name, and writes the files. It prints one line of JSON.

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
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

NOTE_MARKER = "===== NOTE ====="
MAX_NAME_LENGTH = 100
SESSION_TAG = "claude-session"
SESSION_ID = re.compile(r"[0-9A-Za-z-]+")
# Characters Windows forbids in file names, plus ones that break Markdown links.
BAD_NAME_CHARACTERS = re.compile(r'[<>:"/\\|?*#%^\[\]{}()\x00-\x1f]')
RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", "INDEX"} | {
    f"{port}{n}" for port in ("COM", "LPT") for n in range(1, 10)
}
# The table's |---|---| line, with or without spaces and alignment colons.
SEPARATOR_ROW = re.compile(r"\|(\s*:?-+:?\s*\|)+")
INDEX_HEADER = ["| Session | Created | Archived | Note |", "|---|---|---|---|"]
FILE_TOOLS = {"Read", "Write", "Edit", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}
GIT_COMMIT = re.compile(r"\bgit\b[^|;&]*\bcommit\b")
COMMIT_MESSAGE = re.compile(r"""-m\s+(?:"((?:\\.|[^"\\])*)"|'([^']*)')""")
# What git prints after a commit: [branch hash] subject
COMMIT_LINE = re.compile(
    r"^\[(\S+)(?: \(root-commit\))? ([0-9a-f]{7,40})\] (.+)$", re.MULTILINE
)
DOC_WORD = re.compile(r"(^|[^a-z])(design|spec|specs|plan|plans)([^a-z]|$)")
FIELD_LINE = re.compile(r"^([A-Za-z][\w-]*):: ?(.*)$")
QUOTED = re.compile(r'"((?:\\.|[^"\\])*)"')
TAG_LINE = re.compile(r"^#[^\s#]+(\s+#[^\s#]+)*$")


class NoteError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class Commit:
    branch: str
    hash: str | None
    subject: str | None


@dataclass
class Session:
    title: str | None = None
    created: str | None = None
    started: datetime | None = None
    branches: list[str] = field(default_factory=list)
    folders: dict[str, list[str]] = field(default_factory=dict)
    commits: list[Commit] = field(default_factory=list)
    files: list[str] = field(default_factory=list)


@dataclass
class NoteInfo:
    stem: str
    session_id: str | None
    archived: str
    branches: list[str]
    docs: list[str]
    tags: list[str]


def add_unique(items: list, item) -> None:
    if item not in items:
        items.append(item)


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


def commit_message(command: str) -> str | None:
    match = COMMIT_MESSAGE.search(command)
    if not match:
        return None
    text = (
        match.group(1).replace('\\"', '"')
        if match.group(1) is not None
        else match.group(2)
    )
    return text.strip().splitlines()[0] if text.strip() else None


def result_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            item.get("text", "") for item in content if isinstance(item, dict)
        )
    return ""


def read_tools(
    session: Session, entry: dict, branch: str | None, pending: dict
) -> None:
    """Record the files the session touched and the commits it made."""
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "tool_use":
            tool_input = (
                item.get("input") if isinstance(item.get("input"), dict) else {}
            )
            name, command = item.get("name"), tool_input.get("command")
            if name in FILE_TOOLS and isinstance(tool_input.get("file_path"), str):
                add_unique(session.files, tool_input["file_path"])
            if (
                name in SHELL_TOOLS
                and isinstance(command, str)
                and GIT_COMMIT.search(command)
            ):
                pending[item.get("id")] = (branch, commit_message(command))
        elif item.get("type") == "tool_result" and item.get("tool_use_id") in pending:
            used_branch, subject = pending.pop(item["tool_use_id"])
            if item.get("is_error"):
                continue
            found = COMMIT_LINE.findall(result_text(item.get("content")))
            for out_branch, commit_hash, out_subject in found:
                session.commits.append(
                    Commit(out_branch, commit_hash, out_subject.strip())
                )
                add_unique(session.branches, out_branch)
            if not found and used_branch and subject:
                session.commits.append(Commit(used_branch, None, subject))


def read_session(transcripts: list[Path]) -> Session:
    """What the transcripts say: name, start, branches, folders, commits, files."""
    session = Session()
    custom = ai = None
    pending: dict = {}
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
                if stamp and (session.started is None or stamp < session.started):
                    session.started = stamp
                branch = entry.get("gitBranch")
                branch = (
                    branch
                    if isinstance(branch, str) and branch and branch != "HEAD"
                    else None
                )
                cwd = entry.get("cwd")
                if branch:
                    add_unique(session.branches, branch)
                    if isinstance(cwd, str) and cwd:
                        add_unique(session.folders.setdefault(branch, []), cwd)
                read_tools(session, entry, branch, pending)
    session.title = custom or ai
    session.created = (
        session.started.astimezone().date().isoformat() if session.started else None
    )
    return session


def run_git(folder: Path, *args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(
            ["git", "-C", str(folder), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return None


def git_out(folder: Path, *args: str) -> str | None:
    result = run_git(folder, *args)
    return result.stdout.strip() if result and result.returncode == 0 else None


def git_ok(folder: Path, *args: str) -> bool:
    result = run_git(folder, *args)
    return bool(result) and result.returncode == 0


def log_lines(folder: Path, *args: str) -> list[tuple[str, str]]:
    """(short hash, subject) pairs from git log."""
    text = git_out(folder, "log", "--format=%h%x09%s", *args) or ""
    return [tuple(line.split("\t", 1)) for line in text.splitlines() if "\t" in line]


def main_folder(cwd: Path) -> Path:
    """The repository's main folder, also from a worktree or subfolder."""
    common = git_out(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common and Path(common).name == ".git":
        return Path(common).parent.resolve()
    return cwd.resolve()


def note_folder(root: Path, main: Path) -> tuple[Path, str]:
    """<root>/<parent>/<project>, and "parent/project" for display."""
    parts = [part for part in (main.parent.name, main.name) if part]
    return root.joinpath(*parts), "/".join(parts)


def nested_worktrees(folder: Path) -> tuple[str, ...]:
    """Worktree folders inside this folder, as relative paths ending in /."""
    root = folder.resolve()
    skip = []
    for line in (git_out(folder, "worktree", "list", "--porcelain") or "").splitlines():
        if line.startswith("worktree "):
            path = Path(line[len("worktree ") :]).resolve()
            if path != root and path.is_relative_to(root):
                skip.append(path.relative_to(root).as_posix() + "/")
    return tuple(skip)


def is_dirty(folder: Path) -> bool:
    """Uncommitted changes, not counting worktree folders that live inside it."""
    text = git_out(folder, "status", "--porcelain", "--untracked-files=all") or ""
    skip = nested_worktrees(folder)
    return any(
        not line[3:].strip('"').startswith(skip)
        for line in text.splitlines()
        if line.strip()
    )


def worktree_parts(main: Path, folders: list[str]) -> tuple[list[str], bool]:
    """What happened to the branch's worktree folder, and whether it has uncommitted changes."""
    worktrees = [Path(folder) for folder in folders if Path(folder).resolve() != main]
    if not worktrees:
        return [], False
    worktree = worktrees[-1]
    if not worktree.exists():
        return ["worktree removed"], False
    dirty = is_dirty(worktree)
    return [
        "worktree still exists",
        "uncommitted changes" if dirty else "no uncommitted changes",
    ], dirty


def trunk_line(
    main: Path, trunk: str, own: list[Commit], landed: list, used_main: bool
):
    """The commits the session made on the main branch."""
    listed: list = []
    for commit in own:
        found = log_lines(main, "-1", commit.hash) if commit.hash else []
        found = found or [item for item in landed if item[1] == commit.subject][:1]
        for item in found:
            add_unique(listed, item)
    dirty = used_main and is_dirty(main)
    parts = [", ".join(f"`{h}` {s}" for h, s in listed)] if listed else []
    if dirty:
        parts.append("uncommitted changes")
    if not parts:
        return None, None
    return f"- `{trunk}`: " + " · ".join(parts), "uncommitted" if dirty else "merged"


def merged_text(
    main: Path, trunk: str, branch: str, own: list[Commit], landed: list
) -> tuple[str, str]:
    """Where the branch's work stands against the main branch."""
    subjects = {commit.subject for commit in own if commit.subject}
    if git_ok(main, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"):
        ahead = log_lines(main, f"{trunk}..{branch}")
        if not ahead:
            if not own:
                return "no commits", "no-commits"
            tip = log_lines(main, "-1", branch)[0]
            return f"merged into main as `{tip[0]}` {tip[1]}", "merged"
        subjects |= {subject for _, subject in ahead}
        match = [item for item in landed if item[1] in subjects][:1]
        if match:
            return f"merged into main as `{match[0][0]}` {match[0][1]}", "merged"
        changed = (
            git_out(main, "diff", "--name-only", f"{trunk}...{branch}") or ""
        ).splitlines()
        if changed and git_ok(main, "diff", "--quiet", branch, trunk, "--", *changed):
            return "merged into main", "merged"
        return "not merged into main", "unmerged"
    if not own:
        return "no commits", "no-commits"
    on_trunk = [
        commit
        for commit in own
        if commit.hash
        and git_ok(main, "merge-base", "--is-ancestor", commit.hash, trunk)
    ]
    if on_trunk:
        return (
            f"merged into main as `{on_trunk[-1].hash}` {on_trunk[-1].subject}",
            "merged",
        )
    match = [item for item in landed if item[1] in subjects][:1]
    if match:
        return f"merged into main as `{match[0][0]}` {match[0][1]}", "merged"
    return "deleted, not merged into main", "unmerged"


def overall(states: list[str]) -> str:
    for state in ("uncommitted", "unmerged", "merged"):
        if state in states:
            return state
    return "no-commits"


def git_report(main: Path, session: Session) -> tuple[list[str], str, list[str]]:
    """The Git section's lines, the note's status, and the branches it covers."""
    trunk = git_out(main, "rev-parse", "--abbrev-ref", "HEAD")
    if not trunk:
        return [], "no-commits", []
    since = (
        ["--since", session.started.strftime("%Y-%m-%d %H:%M:%S %z")]
        if session.started
        else []
    )
    landed = log_lines(main, trunk, *since)
    used_main = any(
        Path(folder).resolve() == main
        for folders in session.folders.values()
        for folder in folders
    )
    lines, states, branches = [], [], []
    trunk_seen = False
    for branch in session.branches:
        own = [commit for commit in session.commits if commit.branch == branch]
        if branch == trunk:
            trunk_seen = True
            line, state = trunk_line(main, trunk, own, landed, used_main)
            if line:
                lines.append(line)
                states.append(state)
            continue
        text, state = merged_text(main, trunk, branch, own, landed)
        parts, dirty = worktree_parts(main, session.folders.get(branch, []))
        lines.append(f"- `{branch}`: " + " · ".join([text, *parts]))
        states.append("uncommitted" if dirty else state)
        branches.append(branch)
    if not trunk_seen and used_main and is_dirty(main):
        lines.insert(0, f"- `{trunk}`: uncommitted changes")
        states.append("uncommitted")
    return lines, overall(states), branches


def is_doc(relative: Path) -> bool:
    return relative.parts[0].lower() == "docs" or bool(
        DOC_WORD.search(relative.stem.lower())
    )


def find_docs(main: Path, session: Session) -> list[tuple[str, Path]]:
    """Design docs the session read or wrote that still exist, as (repo path, file)."""
    worktrees = {
        Path(folder).resolve()
        for folders in session.folders.values()
        for folder in folders
    } - {main}
    roots = sorted(worktrees, key=lambda root: len(str(root)), reverse=True) + [main]
    docs: list[tuple[str, Path]] = []
    for name in session.files:
        path = Path(name).resolve()
        relative = next(
            (path.relative_to(root) for root in roots if path.is_relative_to(root)),
            None,
        )
        if relative is None or relative.suffix.lower() != ".md" or not is_doc(relative):
            continue
        target = (
            main / relative
            if (main / relative).is_file()
            else path
            if path.is_file()
            else None
        )
        if target and relative.as_posix() not in [doc for doc, _ in docs]:
            docs.append((relative.as_posix(), target))
    return docs


def scan_notes(folder: Path) -> list[NoteInfo]:
    """The fields and tags at the bottom of the project's earlier notes."""
    notes: list[NoteInfo] = []
    if not folder.is_dir():
        return notes
    for path in sorted(folder.glob("*.md")):
        if path.name.lower() == "index.md":
            continue
        fields: dict[str, str] = {}
        tags: list[str] = []
        for text in path.read_text(encoding="utf-8", errors="replace").splitlines():
            text = text.strip()
            match = FIELD_LINE.match(text)
            if match:
                fields[match.group(1).lower()] = match.group(2).strip()
            elif TAG_LINE.match(text):
                tags += [tag[1:] for tag in text.split()]
        notes.append(
            NoteInfo(
                path.stem,
                fields.get("session-id"),
                fields.get("archived", ""),
                QUOTED.findall(fields.get("branches", "")),
                QUOTED.findall(fields.get("docs", "")),
                tags,
            )
        )
    return notes


def find_follows(
    notes: list[NoteInfo], session_id: str, branches: list[str], docs: list[str]
) -> list[str]:
    """Earlier notes that share a branch or a doc with this session, newest first."""
    shared = [
        note
        for note in notes
        if note.session_id != session_id
        and (set(note.branches) & set(branches) or set(note.docs) & set(docs))
    ]
    return [
        note.stem
        for note in sorted(
            shared, key=lambda note: (note.archived, note.stem), reverse=True
        )
    ]


def known_tags(notes: list[NoteInfo]) -> list[str]:
    """Tags already used in the project's notes, most used first."""
    counts = Counter(tag for note in notes for tag in note.tags if tag != SESSION_TAG)
    return [
        tag for tag, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def clean_tags(text: str) -> list[str]:
    """Comma-separated tags as Obsidian tags: lowercase, hyphens, no spaces."""
    tags: list[str] = []
    for raw in text.split(","):
        tag = re.sub(r"\s+", "-", raw.strip().lower().lstrip("#"))
        tag = re.sub(r"[^a-z0-9_/-]", "", tag).strip("-/")
        if tag and not tag.isdigit() and tag != SESSION_TAG:
            add_unique(tags, tag)
    return tags


def file_stem(title: str) -> str:
    stem = " ".join(BAD_NAME_CHARACTERS.sub(" ", title).split())[
        :MAX_NAME_LENGTH
    ].rstrip(" .")
    if not stem:
        return "Untitled session"
    if stem.upper() in RESERVED_NAMES:
        return f"{stem} session"
    return stem


def choose_note_path(folder: Path, stem: str, session_id: str) -> Path:
    """A free file name, or this session's own note from an earlier save."""
    markers = (f"session-id:: {session_id}", f"<!-- session-id: {session_id} -->")
    number = 1
    while True:
        path = folder / (f"{stem}.md" if number == 1 else f"{stem} {number}.md")
        if not path.exists() or any(
            marker in path.read_text(encoding="utf-8", errors="replace")
            for marker in markers
        ):
            return path
        number += 1


def read_draft(path: Path) -> tuple[str, list[str], str]:
    """The summary line, the tags and the note body."""
    if not path.is_file():
        raise NoteError("draft_missing", f"Draft not found: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    marker = next(
        (number for number, text in enumerate(lines) if text.strip() == NOTE_MARKER),
        len(lines),
    )
    fields: dict[str, str] = {}
    for text in lines[:marker]:
        if not text.strip():
            continue
        key, colon, value = text.partition(":")
        if not colon or key.strip().lower() not in ("summary", "tags"):
            raise NoteError(
                "draft_bad_header",
                f"Only summary: and tags: lines go above {NOTE_MARKER}: {text.strip()}",
            )
        fields[key.strip().lower()] = value.strip()
    body = "\n".join(lines[marker + 1 :]).strip()
    if not fields.get("summary"):
        raise NoteError("summary_missing", "The draft has no summary: line.")
    if not body:
        raise NoteError(
            "note_missing", f"The draft has no note after the {NOTE_MARKER} line."
        )
    return fields["summary"], clean_tags(fields.get("tags", "")), body


def quoted_list(items: list[str]) -> str:
    return ", ".join(
        '"' + item.replace("\\", "\\\\").replace('"', '\\"') + '"' for item in items
    )


def compose_note(
    title: str,
    summary: str,
    link: str,
    session_id: str,
    follows: list[str],
    body: str,
    git_lines: list[str],
    docs: list[tuple[str, Path]],
    fields: list[tuple[str, str]],
    tags: list[str],
) -> str:
    link = link.strip().replace(" ", "%20").replace(")", "%29")
    opener = (
        f"[Open session]({link})" if link else f"Resume: `claude --resume {session_id}`"
    )
    parts = [f"# {title}", f"**{summary}** · {opener}"]
    if follows:
        parts.append("Follows: " + ", ".join(f"[[{name}]]" for name in follows))
    parts.append(body)
    if git_lines:
        parts.append("## Git\n" + "\n".join(git_lines))
    if docs:
        parts.append(
            "## Docs\n"
            + "\n".join(f"- [{Path(doc).name}]({path.as_uri()})" for doc, path in docs)
        )
    parts.append("---")
    parts.append("\n".join(f"{key}:: {value}" for key, value in fields))
    parts.append(" ".join(f"#{tag}" for tag in [SESSION_TAG, *tags]))
    return "\n\n".join(parts) + "\n"


def cell(text: str) -> str:
    return text.replace("|", "/").strip()


def update_index(
    index: Path,
    project: str,
    title: str,
    file_name: str,
    created: str,
    archived: str,
    summary: str,
) -> None:
    """Put the note's row at the top of the table, replacing any earlier row for it."""
    link = file_name.replace(" ", "%20")
    link_text = cell(title).replace("[", "(").replace("]", ")")
    row = f"| [{link_text}]({link}) | {created} | {archived} | {cell(summary)} |"
    if index.exists():
        lines = index.read_text(encoding="utf-8").splitlines()
    else:
        lines = [f"# {project} sessions", "", *INDEX_HEADER]
    key = f"]({link})"
    lines = [
        text for text in lines if not (text.lstrip().startswith("|") and key in text)
    ]
    separator = next(
        (
            number
            for number, text in enumerate(lines)
            if SEPARATOR_ROW.fullmatch(text.strip())
        ),
        None,
    )
    if separator is None:
        while lines and not lines[-1].strip():
            lines.pop()
        if lines:
            lines.append("")
        lines += INDEX_HEADER
        separator = len(lines) - 1
    lines.insert(separator + 1, row)
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
    root = notes_root(args.root)
    session = read_session(find_transcripts(Path(args.projects_dir), args.session_id))
    main = main_folder(Path(args.cwd))
    folder, project = note_folder(root, main)
    return {
        "session": session,
        "title": args.title.strip() or session.title,
        "archived": date.today().isoformat(),
        "main": main,
        "folder": folder,
        "project": project,
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
        "title": info["title"],
        "created": info["session"].created,
        "archived": info["archived"],
        "project": info["project"],
        "note_path": str(note) if note else None,
        "index_path": str(info["folder"] / "index.md"),
        "draft_path": str(info["draft"]),
        "known_tags": known_tags(scan_notes(info["folder"])),
    }


def save(args: argparse.Namespace) -> dict:
    info = resolve(args)
    if not info["title"]:
        raise NoteError(
            "title_missing", "The session has no name. Pass one with --title."
        )
    summary, tags, body = read_draft(info["draft"])
    session, folder, main = info["session"], info["folder"], info["main"]
    git_lines, status, branches = git_report(main, session)
    docs = find_docs(main, session)
    follows = find_follows(
        scan_notes(folder), args.session_id, branches, [doc for doc, _ in docs]
    )
    folder.mkdir(parents=True, exist_ok=True)
    note = choose_note_path(folder, file_stem(info["title"]), args.session_id)
    follows = [name for name in follows if name != note.stem]
    created = session.created or "unknown"
    fields = [
        ("summary", summary),
        ("project", info["project"]),
        ("created", created),
        ("archived", info["archived"]),
        ("status", status),
    ]
    if branches:
        fields.append(("branches", quoted_list(branches)))
    if docs:
        fields.append(("docs", quoted_list([doc for doc, _ in docs])))
    fields.append(("session-id", args.session_id))
    text = compose_note(
        info["title"],
        summary,
        args.link,
        args.session_id,
        follows,
        body,
        git_lines,
        docs,
        fields,
        tags,
    )
    write_text(note, text)
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
    parser.add_argument(
        "--title", default="", help="session name to use when the session has none"
    )
    parser.add_argument(
        "--link", default="", help="link that reopens the session in the desktop app"
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
