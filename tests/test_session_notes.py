"""Tests for plugins/session-notes/scripts/session_notes.py."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
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


class TempDirTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        # Keep git from finding a repository above the temp folder.
        env = mock.patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": str(self.tmp)})
        env.start()
        self.addCleanup(env.stop)

    def write(self, name, text):
        path = self.tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


class ParseLevelTest(unittest.TestCase):
    def test_accepts_blank_1_and_2(self):
        for text, want in [("", 1), (" ", 1), ("1", 1), ("2", 2), (" 2 ", 2)]:
            with self.subTest(text=text):
                self.assertEqual(sn.parse_level(text), want)

    def test_rejects_0_3_and_other_text(self):
        for text in ["0", "3", "-1", "abc", "1 2"]:
            with self.subTest(text=text), self.assertRaises(sn.NoteError) as caught:
                sn.parse_level(text)
            self.assertEqual(caught.exception.code, "bad_level")


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


class ReadSessionTest(TempDirTest):
    def test_custom_title_beats_ai_title(self):
        path = self.write(
            "t.jsonl",
            line(type="ai-title", aiTitle="Auto name")
            + line(type="custom-title", customTitle="My name")
            + line(type="ai-title", aiTitle="Later auto name"),
        )
        self.assertEqual(sn.read_session([path])[0], "My name")

    def test_ai_title_used_without_custom_title(self):
        path = self.write("t.jsonl", line(type="ai-title", aiTitle="Auto name"))
        self.assertEqual(sn.read_session([path])[0], "Auto name")

    def test_last_custom_title_wins(self):
        path = self.write(
            "t.jsonl",
            line(type="custom-title", customTitle="Old")
            + line(type="custom-title", customTitle="New"),
        )
        self.assertEqual(sn.read_session([path])[0], "New")

    def test_later_file_wins(self):
        old = self.write("a/t.jsonl", line(type="custom-title", customTitle="Old"))
        new = self.write("b/t.jsonl", line(type="custom-title", customTitle="New"))
        self.assertEqual(sn.read_session([old, new])[0], "New")

    def test_title_text_inside_messages_is_ignored(self):
        path = self.write(
            "t.jsonl",
            line(type="user", message={"type": "custom-title", "customTitle": "Fake"}),
        )
        self.assertIsNone(sn.read_session([path])[0])

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
        self.assertEqual(sn.read_session([path])[1], "2026-09-20")

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
        self.assertEqual(sn.read_session([path])[1], want)

    def test_nothing_found_gives_none(self):
        path = self.write("t.jsonl", line(type="user"))
        self.assertEqual(sn.read_session([path]), (None, None))


class FindTranscriptsTest(TempDirTest):
    def test_finds_the_session_in_any_project_folder(self):
        first = self.write(f"C--proj/{SESSION_ID}.jsonl", "")
        second = self.write(f"C--proj--claude-worktrees-fix/{SESSION_ID}.jsonl", "")
        self.write("C--proj/other.jsonl", "")
        self.assertEqual(
            set(sn.find_transcripts(self.tmp, SESSION_ID)), {first, second}
        )


class MainFolderTest(TempDirTest):
    def git(self, *args):
        subprocess.run(["git", *args], check=True, capture_output=True)

    def make_repo(self):
        repo = self.tmp / "PNFL" / "athc"
        repo.mkdir(parents=True)
        self.git("init", "-q", str(repo))
        self.git(
            "-C",
            str(repo),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "init",
        )
        return repo

    def test_worktree_maps_to_main_folder(self):
        repo = self.make_repo()
        worktree = repo / ".claude" / "worktrees" / "fix"
        self.git("-C", str(repo), "worktree", "add", "-q", "-b", "fix", str(worktree))
        self.assertEqual(sn.main_folder(worktree), repo)

    def test_subfolder_maps_to_repo_folder(self):
        repo = self.make_repo()
        (repo / "docs").mkdir()
        self.assertEqual(sn.main_folder(repo / "docs"), repo)

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


class ReadDraftTest(TempDirTest):
    def test_splits_summary_and_note(self):
        path = self.write(
            "d.md", "Fixed the parser.\n===== NOTE =====\n## Done\n- Fixed it.\n"
        )
        self.assertEqual(
            sn.read_draft(path), (["Fixed the parser."], "## Done\n- Fixed it.")
        )

    def test_accepts_1_and_3_summary_lines(self):
        for summary in (["One."], ["One.", "Two.", "Three."]):
            with self.subTest(lines=len(summary)):
                path = self.write(
                    "d.md", "\n".join(summary) + "\n===== NOTE =====\nBody\n"
                )
                self.assertEqual(sn.read_draft(path)[0], summary)

    def test_rejects_0_and_4_summary_lines(self):
        for summary, code in (
            ([], "summary_missing"),
            (["1.", "2.", "3.", "4."], "summary_too_long"),
        ):
            with (
                self.subTest(lines=len(summary)),
                self.assertRaises(sn.NoteError) as caught,
            ):
                sn.read_draft(
                    self.write(
                        "d.md", "\n".join(summary) + "\n===== NOTE =====\nBody\n"
                    )
                )
            self.assertEqual(caught.exception.code, code)

    def test_rejects_a_draft_without_a_note(self):
        for text in ("Summary.\n", "Summary.\n===== NOTE =====\n \n"):
            with self.subTest(text=text), self.assertRaises(sn.NoteError) as caught:
                sn.read_draft(self.write("d.md", text))
            self.assertEqual(caught.exception.code, "note_missing")

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
        self.write("Fix parser.md", "<!-- session-id: other -->\n")
        self.write("Fix parser 2.md", "<!-- session-id: another -->\n")
        self.assertEqual(
            sn.choose_note_path(self.tmp, "Fix parser", SESSION_ID),
            self.tmp / "Fix parser 3.md",
        )

    def test_same_sessions_note_is_reused(self):
        self.write("Fix parser.md", f"<!-- session-id: {SESSION_ID} -->\n")
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
        text = self.update("Fix parser.md", "2026-09-23", ["Fixed the parser."])
        self.assertEqual(
            text,
            self.HEADER
            + "| [Fix parser](Fix%20parser.md) | 2026-09-20 | 2026-09-23 | Fixed the parser. |\n",
        )

    def test_puts_a_new_note_on_top(self):
        self.update("Fix parser.md", "2026-09-23", ["First."])
        text = self.update("Add tests.md", "2026-09-24", ["Second."], title="Add tests")
        self.assertEqual(
            text,
            self.HEADER
            + "| [Add tests](Add%20tests.md) | 2026-09-20 | 2026-09-24 | Second. |\n"
            + "| [Fix parser](Fix%20parser.md) | 2026-09-20 | 2026-09-23 | First. |\n",
        )

    def test_moves_a_note_saved_again_to_the_top(self):
        self.update("Fix parser.md", "2026-09-23", ["First."])
        self.update("Add tests.md", "2026-09-24", ["Second."], title="Add tests")
        text = self.update("Fix parser.md", "2026-09-25", ["Again."])
        self.assertEqual(
            text,
            self.HEADER
            + "| [Fix parser](Fix%20parser.md) | 2026-09-20 | 2026-09-25 | Again. |\n"
            + "| [Add tests](Add%20tests.md) | 2026-09-20 | 2026-09-24 | Second. |\n",
        )

    def test_replaces_the_row_for_the_same_note(self):
        self.update("Fix parser.md", "2026-09-23", ["First."])
        text = self.update("Fix parser.md", "2026-09-24", ["Again."])
        self.assertEqual(
            text,
            self.HEADER
            + "| [Fix parser](Fix%20parser.md) | 2026-09-20 | 2026-09-24 | Again. |\n",
        )

    def test_keeps_other_text_in_the_index(self):
        table = "| Session | Created | Archived | Note |\n| --- | --- | --- | --- |\n"
        self.write(
            "index.md",
            "# athc sessions\n\nMy notes.\n\n"
            + table
            + "| [A](A.md) | 2026-09-20 | 2026-09-21 | A. |\n\nSee [A](A.md).\n",
        )
        text = self.update("A.md", "2026-09-22", ["A again."], title="A")
        self.assertEqual(
            text,
            "# athc sessions\n\nMy notes.\n\n"
            + table
            + "| [A](A.md) | 2026-09-20 | 2026-09-22 | A again. |\n\nSee [A](A.md).\n",
        )

    def test_adds_a_table_when_the_index_has_none(self):
        self.write("index.md", "# athc sessions\n\nNo table yet.\n\n")
        text = self.update("B.md", "2026-09-22", ["B."], title="B")
        self.assertEqual(
            text,
            "# athc sessions\n\nNo table yet.\n\n"
            "| Session | Created | Archived | Note |\n|---|---|---|---|\n"
            "| [B](B.md) | 2026-09-20 | 2026-09-22 | B. |\n",
        )

    def test_keeps_titles_and_summaries_from_breaking_the_table(self):
        text = self.update(
            "A B.md", "2026-09-23", ["One | two", "Three"], title="A | B [x]"
        )
        self.assertTrue(
            text.endswith(
                "| [A / B (x)](A%20B.md) | 2026-09-20 | 2026-09-23 | One / two<br>Three |\n"
            )
        )


class CommandTest(TempDirTest):
    DRAFT = "Fixed the parser.\n===== NOTE =====\n## Done\n- Fixed it.\n"

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

    def run_command(self, command, *extra, root=None, level="", session_id=SESSION_ID):
        argv = [
            command,
            "--root", str(self.root if root is None else root),
            "--session-id", session_id,
            "--data-dir", str(self.tmp / "data"),
            "--level", level,
            "--projects-dir", str(self.tmp / "projects"),
            "--cwd", str(self.cwd),
            *extra,
        ]  # fmt: skip
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = sn.main(argv)
        return code, json.loads(out.getvalue())

    def test_prepare_reports_the_session(self):
        code, result = self.run_command("prepare", level="2")
        self.assertEqual(code, 0)
        self.assertEqual(
            result,
            {
                "ok": True,
                "level": 2,
                "title": "Fix parser",
                "created": "2026-09-20",
                "archived": self.today,
                "project": "PNFL/athc",
                "note_path": str(self.folder / "Fix parser.md"),
                "index_path": str(self.folder / "index.md"),
                "draft_path": str(self.draft),
            },
        )
        self.assertTrue(self.draft.parent.is_dir())
        self.assertFalse(self.folder.exists())

    def test_prepare_without_a_title_leaves_it_empty(self):
        self.transcript(line(type="user", timestamp="2026-09-20T12:00:00.000Z"))
        code, result = self.run_command("prepare")
        self.assertEqual((code, result["title"], result["note_path"]), (0, None, None))

    def test_prepare_removes_an_old_draft(self):
        self.write(f"data/drafts/{SESSION_ID}.md", self.DRAFT)
        self.run_command("prepare")
        self.assertFalse(self.draft.exists())

    def test_errors_stop_both_commands(self):
        cases = [
            ("root_missing", {"root": self.tmp / "gone"}),
            ("root_not_set", {"root": ""}),
            ("root_not_set", {"root": "${user_config.notes_root}"}),
            ("bad_level", {"level": "3"}),
            ("session_missing", {"session_id": "${CLAUDE_SESSION_ID}"}),
        ]
        for command in ("prepare", "save"):
            for error, options in cases:
                with self.subTest(command=command, error=error, options=options):
                    self.write(f"data/drafts/{SESSION_ID}.md", self.DRAFT)
                    code, result = self.run_command(command, **options)
                    self.assertEqual(
                        (code, result["ok"], result["error"]), (1, False, error)
                    )
                    self.assertFalse(self.folder.exists())

    def test_save_writes_the_note_and_index_and_removes_the_draft(self):
        self.write(f"data/drafts/{SESSION_ID}.md", self.DRAFT)
        code, result = self.run_command("save")
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
            "**Project:** PNFL/athc\n\n"
            f"**Created:** 2026-09-20 · **Archived:** {self.today}\n\n"
            "## Done\n- Fixed it.\n\n"
            f"<!-- session-id: {SESSION_ID} -->\n",
        )
        self.assertIn(
            f"| [Fix parser](Fix%20parser.md) | 2026-09-20 | {self.today} | Fixed the parser. |\n",
            (self.folder / "index.md").read_text(encoding="utf-8"),
        )
        self.assertFalse(self.draft.exists())

    def test_saving_the_same_session_twice_keeps_one_note_and_row(self):
        self.write(f"data/drafts/{SESSION_ID}.md", self.DRAFT)
        self.run_command("save")
        self.write(
            f"data/drafts/{SESSION_ID}.md",
            "Fixed it again.\n===== NOTE =====\n## Done\n- Again.\n",
        )
        self.run_command("save")
        index = (self.folder / "index.md").read_text(encoding="utf-8")
        self.assertEqual(
            sorted(path.name for path in self.folder.iterdir()),
            ["Fix parser.md", "index.md"],
        )
        self.assertEqual(index.count("](Fix%20parser.md)"), 1)
        self.assertIn("| Fixed it again. |", index)

    def test_save_uses_the_given_title_when_the_session_has_none(self):
        self.transcript(line(type="user", timestamp="2026-09-20T12:00:00.000Z"))
        self.write(f"data/drafts/{SESSION_ID}.md", self.DRAFT)
        code, result = self.run_command("save", "--title", "Named by me")
        self.assertEqual(
            (code, result["note_path"]), (0, str(self.folder / "Named by me.md"))
        )

    def test_save_without_any_title_fails_and_keeps_the_draft(self):
        self.transcript(line(type="user", timestamp="2026-09-20T12:00:00.000Z"))
        self.write(f"data/drafts/{SESSION_ID}.md", self.DRAFT)
        code, result = self.run_command("save")
        self.assertEqual((code, result["error"]), (1, "title_missing"))
        self.assertTrue(self.draft.exists())

    def test_unknown_start_date_is_written_as_unknown(self):
        self.transcript(line(type="custom-title", customTitle="Fix parser"))
        self.write(f"data/drafts/{SESSION_ID}.md", self.DRAFT)
        self.run_command("save")
        self.assertIn(
            f"**Created:** unknown · **Archived:** {self.today}",
            (self.folder / "Fix parser.md").read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
