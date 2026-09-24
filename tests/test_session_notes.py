"""Tests for plugins/session-notes/scripts/session_notes.py."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "plugins" / "session-notes" / "scripts"),
)

import session_notes as sn  # noqa: E402

SESSION_ID = "11111111-2222-3333-4444-555555555555"


def line(**fields):
    return json.dumps(fields) + "\n"


def entry(cwd, branch, *content, kind="assistant", timestamp=None):
    fields = {
        "type": kind,
        "cwd": str(cwd),
        "gitBranch": branch,
        "message": {"role": kind, "content": list(content)},
    }
    if timestamp:
        fields["timestamp"] = timestamp
    return line(**fields)


def tool_use(tool_id, name, **tool_input):
    return {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}


def tool_result(tool_id, text, is_error=False):
    return {
        "type": "tool_result",
        "tool_use_id": tool_id,
        "content": text,
        "is_error": is_error,
    }


class TempDirTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        # Keep git from finding a repository above the temp folder.
        env = mock.patch.dict(
            os.environ,
            {
                "GIT_CEILING_DIRECTORIES": str(self.tmp),
                "GIT_AUTHOR_NAME": "t",
                "GIT_AUTHOR_EMAIL": "t@example.com",
                "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@example.com",
            },
        )
        env.start()
        self.addCleanup(env.stop)

    def write(self, name, text):
        path = self.tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


class GitRepoTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.repo = self.tmp / "PNFL" / "athc"
        self.repo.mkdir(parents=True)
        self.git("init", "-q", "-b", "main")
        self.git("commit", "-q", "--allow-empty", "-m", "init")
        self.started = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        )

    def git(self, *args, cwd=None):
        result = subprocess.run(
            ["git", "-C", str(cwd or self.repo), *args],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def worktree(self, branch="fix"):
        path = self.repo / ".claude" / "worktrees" / branch
        self.git("worktree", "add", "-q", "-b", branch, str(path))
        return path

    def commit(self, folder, name, subject):
        (folder / name).write_text(subject, encoding="utf-8")
        self.git("add", "-A", cwd=folder)
        self.git("commit", "-q", "-m", subject, cwd=folder)
        return self.git("log", "-1", "--format=%h", cwd=folder)

    def session(self, *entries):
        path = self.write(
            "t.jsonl",
            entry(self.repo, "main", timestamp=self.started) + "".join(entries),
        )
        return sn.read_session([path])


class FileStemTest(unittest.TestCase):
    def test_drops_characters_windows_forbids(self):
        self.assertEqual(sn.file_stem('Fix: "parser" / CLI?'), "Fix parser CLI")

    def test_drops_characters_that_break_links(self):
        self.assertEqual(
            sn.file_stem("Issue #12 [draft] (v2) 50%"), "Issue 12 draft v2 50"
        )

    def test_drops_trailing_dots(self):
        self.assertEqual(sn.file_stem("Wait..."), "Wait")

    def test_keeps_100_characters(self):
        self.assertEqual(sn.file_stem("a" * 100), "a" * 100)

    def test_cuts_101_characters_to_100(self):
        self.assertEqual(sn.file_stem("a" * 101), "a" * 100)

    def test_empty_name_becomes_untitled(self):
        self.assertEqual(sn.file_stem("???"), "Untitled session")

    def test_reserved_names_get_a_suffix(self):
        for title, want in [
            ("CON", "CON session"),
            ("lpt1", "lpt1 session"),
            ("Index", "Index session"),
        ]:
            with self.subTest(title=title):
                self.assertEqual(sn.file_stem(title), want)


class CleanTagsTest(unittest.TestCase):
    def test_makes_obsidian_tags(self):
        self.assertEqual(
            sn.clean_tags(
                "AI Configuration, #CLI, logging, 1984, logging, claude-session, sub/command"
            ),
            ["ai-configuration", "cli", "logging", "sub/command"],
        )

    def test_empty_gives_no_tags(self):
        self.assertEqual(sn.clean_tags(" , "), [])


class ReadSessionTest(TempDirTest):
    def test_custom_title_beats_ai_title(self):
        path = self.write(
            "t.jsonl",
            line(type="ai-title", aiTitle="Auto name")
            + line(type="custom-title", customTitle="My name")
            + line(type="ai-title", aiTitle="Later auto name"),
        )
        self.assertEqual(sn.read_session([path]).title, "My name")

    def test_ai_title_used_without_custom_title(self):
        path = self.write("t.jsonl", line(type="ai-title", aiTitle="Auto name"))
        self.assertEqual(sn.read_session([path]).title, "Auto name")

    def test_last_custom_title_wins(self):
        path = self.write(
            "t.jsonl",
            line(type="custom-title", customTitle="Old")
            + line(type="custom-title", customTitle="New"),
        )
        self.assertEqual(sn.read_session([path]).title, "New")

    def test_later_file_wins(self):
        old = self.write("a/t.jsonl", line(type="custom-title", customTitle="Old"))
        new = self.write("b/t.jsonl", line(type="custom-title", customTitle="New"))
        self.assertEqual(sn.read_session([old, new]).title, "New")

    def test_title_text_inside_messages_is_ignored(self):
        path = self.write(
            "t.jsonl",
            line(type="user", message={"type": "custom-title", "customTitle": "Fake"}),
        )
        self.assertIsNone(sn.read_session([path]).title)

    def test_created_is_earliest_top_level_timestamp(self):
        path = self.write(
            "t.jsonl",
            line(type="user", timestamp="2026-09-21T12:00:00.000Z")
            + line(
                type="user",
                timestamp="2026-09-20T12:00:00.000Z",
                toolUseResult={"timestamp": "2020-01-01T12:00:00Z"},
            )
            + "not json\n",
        )
        self.assertEqual(sn.read_session([path]).created, "2026-09-20")

    def test_created_is_a_local_date(self):
        path = self.write(
            "t.jsonl", line(type="user", timestamp="2026-09-24T00:22:43.573Z")
        )
        want = (
            datetime(2026, 9, 24, 0, 22, 43, tzinfo=timezone.utc)
            .astimezone()
            .date()
            .isoformat()
        )
        self.assertEqual(sn.read_session([path]).created, want)

    def test_nothing_found_gives_none(self):
        session = sn.read_session([self.write("t.jsonl", line(type="user"))])
        self.assertEqual((session.title, session.created), (None, None))

    def test_reads_branches_folders_commits_and_files(self):
        path = self.write(
            "t.jsonl",
            entry("C:/repo", "main")
            + entry(
                "C:/repo/wt",
                "fix",
                tool_use(
                    "a", "Bash", command='git add -A && git commit -m "Fix parser"'
                ),
            )
            + entry(
                "C:/repo/wt",
                "fix",
                tool_result("a", "[fix 1234567] Fix parser\n 1 file changed"),
                kind="user",
            )
            + entry(
                "C:/repo/wt",
                "fix",
                tool_use("b", "Bash", command="git commit -q -m 'Quiet one'"),
            )
            + entry("C:/repo/wt", "fix", tool_result("b", ""), kind="user")
            + entry(
                "C:/repo/wt",
                "fix",
                tool_use("c", "Bash", command='git commit -m "Refused"'),
            )
            + entry(
                "C:/repo/wt",
                "fix",
                tool_result("c", "denied", is_error=True),
                kind="user",
            )
            + entry(
                "C:/repo/wt",
                "fix",
                tool_use(
                    "d", "Write", file_path="C:/repo/wt/docs/plan.md", content="x"
                ),
            ),
        )
        session = sn.read_session([path])
        self.assertEqual(session.branches, ["main", "fix"])
        self.assertEqual(session.folders, {"main": ["C:/repo"], "fix": ["C:/repo/wt"]})
        self.assertEqual(
            session.commits,
            [
                sn.Commit("fix", "1234567", "Fix parser"),
                sn.Commit("fix", None, "Quiet one"),
            ],
        )
        self.assertEqual(session.files, ["C:/repo/wt/docs/plan.md"])


class FindTranscriptsTest(TempDirTest):
    def test_finds_the_session_in_any_project_folder(self):
        first = self.write(f"C--proj/{SESSION_ID}.jsonl", "")
        second = self.write(f"C--proj--claude-worktrees-fix/{SESSION_ID}.jsonl", "")
        self.write("C--proj/other.jsonl", "")
        self.assertEqual(
            set(sn.find_transcripts(self.tmp, SESSION_ID)), {first, second}
        )


class MainFolderTest(GitRepoTest):
    def test_worktree_maps_to_main_folder(self):
        self.assertEqual(sn.main_folder(self.worktree()), self.repo)

    def test_subfolder_maps_to_repo_folder(self):
        (self.repo / "docs").mkdir()
        self.assertEqual(sn.main_folder(self.repo / "docs"), self.repo)

    def test_folder_outside_git_is_used_as_is(self):
        folder = self.tmp / "PNFL" / "notes"
        folder.mkdir(parents=True)
        self.assertEqual(sn.main_folder(folder), folder)


class NoteFolderTest(unittest.TestCase):
    def test_uses_parent_and_project_names(self):
        root = Path("R:/notes")
        self.assertEqual(
            sn.note_folder(root, Path("C:/Projects/PNFL/athc")),
            (root / "PNFL" / "athc", "PNFL/athc"),
        )

    def test_project_at_drive_root_has_no_parent_folder(self):
        root = Path("R:/notes")
        main = Path(Path.cwd().anchor) / "proj"
        self.assertEqual(sn.note_folder(root, main), (root / "proj", "proj"))


class GitReportTest(GitRepoTest):
    def branch_entries(self, folder, subject, result, tool_id="a"):
        return entry(
            folder,
            "fix",
            tool_use(tool_id, "Bash", command=f'git commit -m "{subject}"'),
        ) + entry(folder, "fix", tool_result(tool_id, result), kind="user")

    def test_squash_merged_branch_shows_the_main_commit(self):
        wt = self.worktree()
        tip = self.commit(wt, "a.txt", "Fix parser")
        self.git("merge", "--squash", "fix")
        self.git("commit", "-q", "-m", "Fix parser")
        landed = self.git("log", "-1", "--format=%h")
        self.git("worktree", "remove", str(wt))
        self.git("branch", "-D", "fix")
        session = self.session(
            self.branch_entries(wt, "Fix parser", f"[fix {tip}] Fix parser")
        )
        self.assertEqual(
            sn.git_report(self.repo, session),
            (
                [
                    f"- `fix`: merged into main as `{landed}` Fix parser · worktree removed"
                ],
                "merged",
                ["fix"],
            ),
        )

    def test_unmerged_branch_says_so(self):
        wt = self.worktree()
        tip = self.commit(wt, "a.txt", "Fix parser")
        session = self.session(
            self.branch_entries(wt, "Fix parser", f"[fix {tip}] Fix parser")
        )
        self.assertEqual(
            sn.git_report(self.repo, session),
            (
                [
                    "- `fix`: not merged into main · worktree still exists · no uncommitted changes"
                ],
                "unmerged",
                ["fix"],
            ),
        )

    def test_uncommitted_changes_show(self):
        wt = self.worktree()
        tip = self.commit(wt, "a.txt", "Fix parser")
        (wt / "a.txt").write_text("changed", encoding="utf-8")
        session = self.session(
            self.branch_entries(wt, "Fix parser", f"[fix {tip}] Fix parser")
        )
        self.assertEqual(
            sn.git_report(self.repo, session),
            (
                [
                    "- `fix`: not merged into main · worktree still exists · uncommitted changes"
                ],
                "uncommitted",
                ["fix"],
            ),
        )

    def test_fast_forward_merge_counts_as_merged(self):
        wt = self.worktree()
        tip = self.commit(wt, "a.txt", "Add x")
        self.git("merge", "-q", "--ff-only", "fix")
        self.git("worktree", "remove", str(wt))
        self.git("branch", "-D", "fix")
        session = self.session(self.branch_entries(wt, "Add x", f"[fix {tip}] Add x"))
        self.assertEqual(
            sn.git_report(self.repo, session),
            (
                [f"- `fix`: merged into main as `{tip}` Add x · worktree removed"],
                "merged",
                ["fix"],
            ),
        )

    def test_a_new_message_on_main_still_counts_when_the_files_match(self):
        wt = self.worktree()
        tip = self.commit(wt, "a.txt", "WIP")
        self.git("merge", "--squash", "fix")
        self.git("commit", "-q", "-m", "Add feature")
        self.git("worktree", "remove", str(wt))
        session = self.session(self.branch_entries(wt, "WIP", f"[fix {tip}] WIP"))
        self.assertEqual(
            sn.git_report(self.repo, session),
            (["- `fix`: merged into main · worktree removed"], "merged", ["fix"]),
        )

    def test_deleted_branch_missing_from_main_says_so(self):
        wt = self.worktree()
        tip = self.commit(wt, "a.txt", "Lost work")
        self.git("worktree", "remove", str(wt))
        self.git("branch", "-D", "fix")
        session = self.session(
            self.branch_entries(wt, "Lost work", f"[fix {tip}] Lost work")
        )
        self.assertEqual(
            sn.git_report(self.repo, session),
            (
                ["- `fix`: deleted, not merged into main · worktree removed"],
                "unmerged",
                ["fix"],
            ),
        )

    def test_branch_without_commits(self):
        wt = self.worktree()
        session = self.session(entry(wt, "fix"))
        self.assertEqual(
            sn.git_report(self.repo, session),
            (
                [
                    "- `fix`: no commits · worktree still exists · no uncommitted changes"
                ],
                "no-commits",
                ["fix"],
            ),
        )

    def test_commits_on_main_are_listed(self):
        first = self.commit(self.repo, "a.txt", "Update docs")
        second = self.commit(self.repo, "b.txt", "Fix typo")
        session = self.session(
            entry(
                self.repo,
                "main",
                tool_use("a", "Bash", command='git commit -m "Update docs"'),
            ),
            entry(
                self.repo,
                "main",
                tool_result("a", f"[main {first}] Update docs"),
                kind="user",
            ),
            entry(
                self.repo,
                "main",
                tool_use("b", "Bash", command='git commit -q -m "Fix typo"'),
            ),
            entry(self.repo, "main", tool_result("b", ""), kind="user"),
        )
        self.assertEqual(
            sn.git_report(self.repo, session),
            ([f"- `main`: `{first}` Update docs, `{second}` Fix typo"], "merged", []),
        )

    def test_uncommitted_changes_on_main_show(self):
        (self.repo / "a.txt").write_text("new", encoding="utf-8")
        session = self.session()
        self.assertEqual(
            sn.git_report(self.repo, session),
            (["- `main`: uncommitted changes"], "uncommitted", []),
        )

    def test_folder_outside_git_has_no_report(self):
        folder = self.tmp / "plain"
        folder.mkdir()
        self.assertEqual(sn.git_report(folder, sn.Session()), ([], "no-commits", []))


class FindDocsTest(GitRepoTest):
    def test_links_design_docs_that_still_exist(self):
        self.write("PNFL/athc/docs/parser.md", "x")
        self.write("PNFL/athc/notes/2026-09-23-foo-design.md", "x")
        self.write("PNFL/athc/src/explanation.md", "x")
        session = self.session(
            entry(
                self.repo,
                "main",
                tool_use("a", "Write", file_path=str(self.repo / "docs" / "parser.md")),
            ),
            entry(
                self.repo,
                "main",
                tool_use(
                    "b",
                    "Read",
                    file_path=str(self.repo / "notes" / "2026-09-23-foo-design.md"),
                ),
            ),
            entry(
                self.repo,
                "main",
                tool_use(
                    "c", "Edit", file_path=str(self.repo / "src" / "explanation.md")
                ),
            ),
            entry(
                self.repo,
                "main",
                tool_use("d", "Write", file_path=str(self.repo / "docs" / "gone.md")),
            ),
        )
        self.assertEqual(
            sn.find_docs(self.repo, session),
            [
                ("docs/parser.md", self.repo / "docs" / "parser.md"),
                (
                    "notes/2026-09-23-foo-design.md",
                    self.repo / "notes" / "2026-09-23-foo-design.md",
                ),
            ],
        )

    def test_worktree_doc_links_to_the_main_copy(self):
        wt = self.worktree()
        self.write("PNFL/athc/docs/plan.md", "main copy")
        (wt / "docs").mkdir()
        (wt / "docs" / "plan.md").write_text("worktree copy", encoding="utf-8")
        session = self.session(
            entry(
                wt,
                "fix",
                tool_use("a", "Write", file_path=str(wt / "docs" / "plan.md")),
            )
        )
        self.assertEqual(
            sn.find_docs(self.repo, session),
            [("docs/plan.md", self.repo / "docs" / "plan.md")],
        )


class ScanNotesTest(TempDirTest):
    def note(
        self,
        name,
        archived,
        session_id="other",
        branches="",
        docs="",
        tags="#claude-session",
    ):
        fields = f"archived:: {archived}\nsession-id:: {session_id}\n"
        if branches:
            fields += f"branches:: {branches}\n"
        if docs:
            fields += f"docs:: {docs}\n"
        self.write(
            f"notes/{name}.md", f"# {name}\n\n## Done\n- x\n\n---\n\n{fields}\n{tags}\n"
        )

    def test_follows_notes_sharing_a_branch_or_doc(self):
        self.note("A", "2026-09-20", branches='"fix"')
        self.note("B", "2026-09-21", docs='"docs/plan.md"')
        self.note("C", "2026-09-22", branches='"other"')
        self.write("notes/index.md", '# athc sessions\n\nbranches:: "fix"\n')
        notes = sn.scan_notes(self.tmp / "notes")
        self.assertEqual(
            sn.find_follows(notes, SESSION_ID, ["fix"], ["docs/plan.md"]), ["B", "A"]
        )

    def test_follows_skips_the_same_session(self):
        self.note("A", "2026-09-20", session_id=SESSION_ID, branches='"fix"')
        notes = sn.scan_notes(self.tmp / "notes")
        self.assertEqual(sn.find_follows(notes, SESSION_ID, ["fix"], []), [])

    def test_known_tags_come_from_earlier_notes(self):
        self.note("A", "2026-09-20", tags="#claude-session #logging #cli")
        self.note("B", "2026-09-21", tags="#claude-session #logging")
        self.assertEqual(
            sn.known_tags(sn.scan_notes(self.tmp / "notes")), ["logging", "cli"]
        )

    def test_missing_folder_has_no_notes(self):
        self.assertEqual(sn.scan_notes(self.tmp / "none"), [])


class ReadDraftTest(TempDirTest):
    def test_reads_summary_tags_and_note(self):
        path = self.write(
            "d.md",
            "summary: athc: Added X\ntags: logging, cli\n===== NOTE =====\n## Done\n- X\n",
        )
        self.assertEqual(
            sn.read_draft(path), ("athc: Added X", ["logging", "cli"], "## Done\n- X")
        )

    def test_tags_are_optional(self):
        path = self.write("d.md", "summary: athc: Added X\n\n===== NOTE =====\nBody\n")
        self.assertEqual(sn.read_draft(path), ("athc: Added X", [], "Body"))

    def test_rejects_bad_drafts(self):
        for text, code in [
            ("tags: cli\n===== NOTE =====\nBody\n", "summary_missing"),
            ("summary: \n===== NOTE =====\nBody\n", "summary_missing"),
            (
                "summary: X\nSomething else\n===== NOTE =====\nBody\n",
                "draft_bad_header",
            ),
            ("summary: X\n", "note_missing"),
            ("summary: X\n===== NOTE =====\n \n", "note_missing"),
        ]:
            with self.subTest(text=text), self.assertRaises(sn.NoteError) as caught:
                sn.read_draft(self.write("d.md", text))
            self.assertEqual(caught.exception.code, code)

    def test_rejects_a_missing_draft(self):
        with self.assertRaises(sn.NoteError) as caught:
            sn.read_draft(self.tmp / "none.md")
        self.assertEqual(caught.exception.code, "draft_missing")


class ChooseNotePathTest(TempDirTest):
    def test_new_note_uses_the_plain_name(self):
        self.assertEqual(
            sn.choose_note_path(self.tmp, "Fix parser", SESSION_ID),
            self.tmp / "Fix parser.md",
        )

    def test_another_sessions_note_is_not_overwritten(self):
        self.write("Fix parser.md", "session-id:: other\n")
        self.write("Fix parser 2.md", "<!-- session-id: another -->\n")
        self.assertEqual(
            sn.choose_note_path(self.tmp, "Fix parser", SESSION_ID),
            self.tmp / "Fix parser 3.md",
        )

    def test_same_sessions_note_is_reused(self):
        for text in (
            f"session-id:: {SESSION_ID}\n",
            f"<!-- session-id: {SESSION_ID} -->\n",
        ):
            with self.subTest(text=text):
                self.write("Fix parser.md", text)
                self.assertEqual(
                    sn.choose_note_path(self.tmp, "Fix parser", SESSION_ID),
                    self.tmp / "Fix parser.md",
                )


class UpdateIndexTest(TempDirTest):
    HEADER = "# athc sessions\n\n| Session | Created | Archived | Note |\n|---|---|---|---|\n"

    def update(self, file_name, archived, summary, title="Fix parser"):
        sn.update_index(
            self.tmp / "index.md",
            "athc",
            title,
            file_name,
            "2026-09-20",
            archived,
            summary,
        )
        return (self.tmp / "index.md").read_text(encoding="utf-8")

    def test_creates_the_index_with_one_row(self):
        text = self.update("Fix parser.md", "2026-09-23", "athc: Fixed the parser")
        self.assertEqual(
            text,
            self.HEADER
            + "| [Fix parser](Fix%20parser.md) | 2026-09-20 | 2026-09-23 | athc: Fixed the parser |\n",
        )

    def test_puts_a_new_note_on_top(self):
        self.update("Fix parser.md", "2026-09-23", "First.")
        text = self.update("Add tests.md", "2026-09-24", "Second.", title="Add tests")
        self.assertEqual(
            text,
            self.HEADER
            + "| [Add tests](Add%20tests.md) | 2026-09-20 | 2026-09-24 | Second. |\n"
            + "| [Fix parser](Fix%20parser.md) | 2026-09-20 | 2026-09-23 | First. |\n",
        )

    def test_moves_a_note_saved_again_to_the_top(self):
        self.update("Fix parser.md", "2026-09-23", "First.")
        self.update("Add tests.md", "2026-09-24", "Second.", title="Add tests")
        text = self.update("Fix parser.md", "2026-09-25", "Again.")
        self.assertEqual(
            text,
            self.HEADER
            + "| [Fix parser](Fix%20parser.md) | 2026-09-20 | 2026-09-25 | Again. |\n"
            + "| [Add tests](Add%20tests.md) | 2026-09-20 | 2026-09-24 | Second. |\n",
        )

    def test_keeps_other_text_in_the_index(self):
        table = "| Session | Created | Archived | Note |\n| --- | --- | --- | --- |\n"
        self.write(
            "index.md",
            "# athc sessions\n\nMy notes.\n\n"
            + table
            + "| [A](A.md) | 2026-09-20 | 2026-09-21 | A. |\n\nSee [A](A.md).\n",
        )
        text = self.update("A.md", "2026-09-22", "A again.", title="A")
        self.assertEqual(
            text,
            "# athc sessions\n\nMy notes.\n\n"
            + table
            + "| [A](A.md) | 2026-09-20 | 2026-09-22 | A again. |\n\nSee [A](A.md).\n",
        )

    def test_adds_a_table_when_the_index_has_none(self):
        self.write("index.md", "# athc sessions\n\nNo table yet.\n\n")
        text = self.update("B.md", "2026-09-22", "B.", title="B")
        self.assertEqual(
            text,
            "# athc sessions\n\nNo table yet.\n\n"
            "| Session | Created | Archived | Note |\n|---|---|---|---|\n"
            "| [B](B.md) | 2026-09-20 | 2026-09-22 | B. |\n",
        )

    def test_keeps_titles_and_summaries_from_breaking_the_table(self):
        text = self.update("A B.md", "2026-09-23", "One | two", title="A | B [x]")
        self.assertTrue(
            text.endswith(
                "| [A / B (x)](A%20B.md) | 2026-09-20 | 2026-09-23 | One / two |\n"
            )
        )


class CommandTest(TempDirTest):
    DRAFT = "summary: athc: Fixed the parser\ntags: logging\n===== NOTE =====\n## Done\n- Fixed it.\n"

    def setUp(self):
        super().setUp()
        self.root = self.tmp / "notes"
        self.root.mkdir()
        self.cwd = self.tmp / "PNFL" / "athc"
        self.cwd.mkdir(parents=True)
        self.folder = self.root / "PNFL" / "athc"
        self.draft = self.tmp / "data" / "drafts" / f"{SESSION_ID}.md"
        self.today = date.today().isoformat()
        self.transcript(
            line(type="user", timestamp="2026-09-20T12:00:00.000Z")
            + line(type="custom-title", customTitle="Fix parser")
        )

    def transcript(self, text):
        self.write(f"projects/C--PNFL-athc/{SESSION_ID}.jsonl", text)

    def run_command(self, command, *extra, root=None, session_id=SESSION_ID):
        argv = [
            command,
            "--root", str(self.root if root is None else root),
            "--session-id", session_id,
            "--data-dir", str(self.tmp / "data"),
            "--projects-dir", str(self.tmp / "projects"),
            "--cwd", str(self.cwd),
            *extra,
        ]  # fmt: skip
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = sn.main(argv)
        return code, json.loads(out.getvalue())

    def save_draft(self, text=DRAFT):
        self.write(f"data/drafts/{SESSION_ID}.md", text)

    def test_prepare_reports_the_session(self):
        self.write(
            "notes/PNFL/athc/Old.md",
            "# Old\n\n---\n\nsession-id:: other\n\n#claude-session #cli\n",
        )
        code, result = self.run_command("prepare")
        self.assertEqual(code, 0)
        self.assertEqual(
            result,
            {
                "ok": True,
                "title": "Fix parser",
                "created": "2026-09-20",
                "archived": self.today,
                "project": "PNFL/athc",
                "note_path": str(self.folder / "Fix parser.md"),
                "index_path": str(self.folder / "index.md"),
                "draft_path": str(self.draft),
                "known_tags": ["cli"],
            },
        )
        self.assertTrue(self.draft.parent.is_dir())

    def test_prepare_without_a_title_leaves_it_empty(self):
        self.transcript(line(type="user", timestamp="2026-09-20T12:00:00.000Z"))
        code, result = self.run_command("prepare")
        self.assertEqual((code, result["title"], result["note_path"]), (0, None, None))

    def test_prepare_removes_an_old_draft(self):
        self.save_draft()
        self.run_command("prepare")
        self.assertFalse(self.draft.exists())

    def test_errors_stop_both_commands(self):
        cases = [
            ("root_missing", {"root": self.tmp / "gone"}),
            ("root_not_set", {"root": ""}),
            ("root_not_set", {"root": "${user_config.notes_root}"}),
            ("session_missing", {"session_id": "${CLAUDE_SESSION_ID}"}),
        ]
        for command in ("prepare", "save"):
            for error, options in cases:
                with self.subTest(command=command, error=error, options=options):
                    self.save_draft()
                    code, result = self.run_command(command, **options)
                    self.assertEqual(
                        (code, result["ok"], result["error"]), (1, False, error)
                    )
                    self.assertFalse(self.folder.exists())

    def test_save_writes_the_note_and_index_and_removes_the_draft(self):
        self.save_draft()
        code, result = self.run_command(
            "save", "--link", "claude://claude.ai/epitaxy/local_1"
        )
        self.assertEqual(
            (code, result),
            (
                0,
                {
                    "ok": True,
                    "note_path": str(self.folder / "Fix parser.md"),
                    "index_path": str(self.folder / "index.md"),
                },
            ),
        )
        self.assertEqual(
            (self.folder / "Fix parser.md").read_text(encoding="utf-8"),
            "# Fix parser\n\n"
            "**athc: Fixed the parser** · [Open session](claude://claude.ai/epitaxy/local_1)\n\n"
            "## Done\n- Fixed it.\n\n"
            "---\n\n"
            "summary:: athc: Fixed the parser\n"
            "project:: PNFL/athc\n"
            "created:: 2026-09-20\n"
            f"archived:: {self.today}\n"
            "status:: no-commits\n"
            f"session-id:: {SESSION_ID}\n\n"
            "#claude-session #logging\n",
        )
        self.assertIn(
            f"| [Fix parser](Fix%20parser.md) | 2026-09-20 | {self.today} | athc: Fixed the parser |\n",
            (self.folder / "index.md").read_text(encoding="utf-8"),
        )
        self.assertFalse(self.draft.exists())

    def test_without_a_link_the_note_shows_how_to_resume(self):
        self.save_draft()
        self.run_command("save")
        self.assertIn(
            f"**athc: Fixed the parser** · Resume: `claude --resume {SESSION_ID}`\n",
            (self.folder / "Fix parser.md").read_text(encoding="utf-8"),
        )

    def test_links_docs_and_follows_earlier_notes_that_share_them(self):
        plan = self.write("PNFL/athc/docs/plan.md", "plan")
        self.transcript(
            line(type="user", timestamp="2026-09-20T12:00:00.000Z")
            + line(type="custom-title", customTitle="Fix parser")
            + entry(self.cwd, "fix", tool_use("a", "Read", file_path=str(plan)))
        )
        self.write(
            "notes/PNFL/athc/Start parser.md",
            '# Start\n\n---\n\ndocs:: "docs/plan.md"\nsession-id:: other\n',
        )
        self.save_draft()
        self.run_command("save")
        text = (self.folder / "Fix parser.md").read_text(encoding="utf-8")
        self.assertIn("\n\nFollows: [[Start parser]]\n\n## Done\n", text)
        self.assertIn(f"\n\n## Docs\n- [plan.md]({plan.as_uri()})\n\n---\n", text)
        self.assertIn('\ndocs:: "docs/plan.md"\n', text)

    def test_saving_the_same_session_twice_keeps_one_note_and_row(self):
        self.save_draft()
        self.run_command("save")
        self.save_draft(
            "summary: athc: Fixed it again\n===== NOTE =====\n## Done\n- Again.\n"
        )
        self.run_command("save")
        index = (self.folder / "index.md").read_text(encoding="utf-8")
        self.assertEqual(
            sorted(path.name for path in self.folder.iterdir()),
            ["Fix parser.md", "index.md"],
        )
        self.assertEqual(index.count("](Fix%20parser.md)"), 1)
        self.assertIn("| athc: Fixed it again |", index)

    def test_save_uses_the_given_title_when_the_session_has_none(self):
        self.transcript(line(type="user", timestamp="2026-09-20T12:00:00.000Z"))
        self.save_draft()
        code, result = self.run_command("save", "--title", "Named by me")
        self.assertEqual(
            (code, result["note_path"]), (0, str(self.folder / "Named by me.md"))
        )

    def test_save_without_any_title_fails_and_keeps_the_draft(self):
        self.transcript(line(type="user", timestamp="2026-09-20T12:00:00.000Z"))
        self.save_draft()
        code, result = self.run_command("save")
        self.assertEqual((code, result["error"]), (1, "title_missing"))
        self.assertTrue(self.draft.exists())

    def test_unknown_start_date_is_written_as_unknown(self):
        self.transcript(line(type="custom-title", customTitle="Fix parser"))
        self.save_draft()
        self.run_command("save")
        self.assertIn(
            "\ncreated:: unknown\n",
            (self.folder / "Fix parser.md").read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
