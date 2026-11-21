from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from ela.tools import (
    _find_pwsh,
    execute_tool,
    tool_edit,
    tool_read,
    tool_search,
    tool_write,
)


class ToolTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cwd = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name: str, text: str) -> Path:
        path = self.cwd / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_read_has_line_numbers_and_range(self):
        path = self.write("a.py", "one\ntwo\nthree\nfour\n")
        out = tool_read({"paths": [str(path)], "start": 2, "end": 3}, self.cwd)
        self.assertIn("lines 2-3 of 4", out)
        self.assertIn("2| two", out)
        self.assertIn("3| three", out)
        self.assertNotIn("4| four", out)

    def test_read_rejects_binary(self):
        path = self.cwd / "bin.dat"
        path.write_bytes(b"abc\x00def")
        out = tool_read({"paths": [str(path)]}, self.cwd)
        self.assertIn("Error: binary file", out)

    def test_read_multiple_files(self):
        a = self.write("a.txt", "alpha\n")
        b = self.write("b.txt", "beta\n")
        out = tool_read({"paths": [str(a), str(b)]}, self.cwd)
        self.assertIn("alpha", out)
        self.assertIn("beta", out)

    def test_read_reports_relative_path(self):
        self.write("a.py", "alpha\n")
        out = tool_read({"paths": ["a.py"]}, self.cwd)
        self.assertIn("a.py (lines 1-1 of 1)", out)
        self.assertNotIn(str(self.cwd), out)

    def test_write_creates_parents_and_uses_lf(self):
        out = tool_write({"path": "nested/x.txt", "content": "a\nb\n"}, self.cwd)
        self.assertIn("Wrote", out)
        data = (self.cwd / "nested" / "x.txt").read_bytes()
        self.assertEqual(data, b"a\nb\n")

    def test_edit_exact(self):
        path = self.write("x.txt", "hello world\n")
        out = tool_edit({"path": str(path), "old": "world", "new": "ela"}, self.cwd)
        self.assertIn("1 replacement", out)
        self.assertEqual(path.read_text(encoding="utf-8"), "hello ela\n")

    def test_edit_ambiguous(self):
        path = self.write("x.txt", "foo foo\n")
        out = execute_tool("edit", {"path": str(path), "old": "foo", "new": "bar"}, self.cwd)
        self.assertIn("appears 2 times", out)
        self.assertEqual(path.read_text(encoding="utf-8"), "foo foo\n")

    def test_edit_replace_all(self):
        path = self.write("x.txt", "foo foo\n")
        out = tool_edit(
            {"path": str(path), "old": "foo", "new": "bar", "replace_all": True},
            self.cwd,
        )
        self.assertIn("2 replacements", out)
        self.assertEqual(path.read_text(encoding="utf-8"), "bar bar\n")

    def test_edit_crlf_file(self):
        path = self.cwd / "win.txt"
        path.write_bytes(b"one\r\ntwo\r\n")
        out = tool_edit({"path": str(path), "old": "two\n", "new": "three\n"}, self.cwd)
        self.assertIn("1 replacement", out)
        self.assertEqual(path.read_bytes(), b"one\r\nthree\r\n")

    def test_edit_missing_gives_hint(self):
        path = self.write("x.txt", "alpha beta\n")
        out = execute_tool(
            "edit", {"path": str(path), "old": "alpha   beta", "new": "x"}, self.cwd
        )
        self.assertIn("whitespace differs", out)

    def test_pwsh_output_and_exit(self):
        out = execute_tool("pwsh", {"command": "Write-Output hello"}, self.cwd)
        self.assertIn("hello", out)
        self.assertIn("exit 0", out)

    def test_pwsh_failure_is_a_value(self):
        out = execute_tool("pwsh", {"command": "exit 3"}, self.cwd)
        self.assertIn("exit 3", out)

    @unittest.skipUnless(os.name == "nt", "cmd.exe only")
    def test_pwsh_preserves_native_exit_code(self):
        out = execute_tool("pwsh", {"command": "cmd /c exit 3"}, self.cwd)
        self.assertIn("exit 3", out)

    def test_pwsh_missing_gives_install_hint(self):
        with mock.patch("ela.tools._find_pwsh", return_value=None):
            out = execute_tool(
                "pwsh", {"command": "Write-Output hi"}, self.cwd
            )
        self.assertIn("PowerShell 7", out)
        self.assertIn("ELA_PWSH", out)

    def test_pwsh_timeout_kills_process_tree_and_returns(self):
        started = time.perf_counter()
        out = execute_tool(
            "pwsh", {"command": "Start-Sleep -Seconds 30", "timeout": 1}, self.cwd
        )
        elapsed = time.perf_counter() - started
        self.assertIn("timed out", out)
        self.assertLess(elapsed, 10)

    def test_pwsh_stdin_is_closed(self):
        started = time.perf_counter()
        out = execute_tool(
            "pwsh", {"command": "[Console]::In.ReadLine()", "timeout": 5}, self.cwd
        )
        elapsed = time.perf_counter() - started
        self.assertIn("exit 0", out)
        self.assertLess(elapsed, 5)

    def test_pwsh_output_is_capped(self):
        started = time.perf_counter()
        out = execute_tool(
            "pwsh",
            {"command": "while ($true) { [Console]::Out.Write('x' * 100000) }", "timeout": 20},
            self.cwd,
        )
        elapsed = time.perf_counter() - started
        self.assertIn("output exceeded", out)
        self.assertLess(elapsed, 10)

    def test_search_content(self):
        self.write("a.py", "print('TODO')\n")
        self.write("b.txt", "nothing\n")
        out = tool_search({"pattern": "TODO", "mode": "content"}, self.cwd)
        self.assertIn("a.py", out)
        self.assertNotIn("b.txt", out)

    def test_search_files(self):
        self.write("src/a.py", "x\n")
        self.write("src/b.txt", "x\n")
        out = tool_search({"pattern": "**/*.py", "mode": "files"}, self.cwd)
        self.assertIn("src/a.py", out)
        self.assertNotIn("src/b.txt", out)

    def test_search_files_recursive_and_skips_node_modules(self):
        self.write("deep/nested/a.py", "x\n")
        self.write("node_modules/pkg/b.py", "x\n")
        out = tool_search({"pattern": "*.py", "mode": "files"}, self.cwd)
        self.assertIn("deep/nested/a.py", out)
        self.assertNotIn("node_modules", out)

    def test_search_content_skips_node_modules(self):
        self.write("a.py", "TODO\n")
        self.write("node_modules/pkg/b.py", "TODO\n")
        out = tool_search({"pattern": "TODO", "mode": "content"}, self.cwd)
        self.assertIn("a.py", out)
        self.assertNotIn("node_modules", out)

    def test_search_single_file(self):
        self.write("a.py", "TODO\n")
        self.write("b.py", "TODO\n")
        out = tool_search(
            {"pattern": "TODO", "path": "a.py", "mode": "content"}, self.cwd
        )
        self.assertIn("a.py", out)
        self.assertNotIn("b.py", out)

    def test_search_count(self):
        self.write("a.py", "TODO\nTODO\n")
        out = tool_search({"pattern": "TODO", "mode": "count"}, self.cwd)
        self.assertIn("2", out)
        self.assertIn("a.py", out)

    def test_search_count_is_sorted_with_total(self):
        self.write("a.py", "TODO\nTODO\nTODO\n")
        self.write("b.py", "TODO\n")
        self.write("c.txt", "TODO\nTODO\n")
        out = tool_search({"pattern": "TODO", "mode": "count"}, self.cwd)
        lines = out.splitlines()
        self.assertEqual(len(lines), 4)
        self.assertIn("3", lines[0])
        self.assertIn("a.py", lines[0])
        self.assertIn("2", lines[1])
        self.assertIn("c.txt", lines[1])
        self.assertEqual(lines[-1], "total 6 matches in 3 files")

    def test_search_count_fallback_is_sorted(self):
        self.write("a.py", "TODO\nTODO\nTODO\n")
        self.write("b.py", "TODO\n")
        with mock.patch("ela.tools.shutil.which", return_value=None):
            out = tool_search({"pattern": "TODO", "mode": "count"}, self.cwd)
        self.assertEqual(out.splitlines()[-1], "total 4 matches in 2 files")
        self.assertIn("a.py", out.splitlines()[0])


    def test_read_batch_has_no_silent_twenty_path_cap(self):
        paths = [
            str(self.write(f"f{index}.txt", f"line-{index}\n")) for index in range(21)
        ]
        out = tool_read({"paths": paths}, self.cwd)
        for index in range(21):
            self.assertIn(f"f{index}.txt", out)

    def test_edit_replace_all_string_false_is_not_truthy(self):
        path = self.write("x.txt", "foo foo\n")
        out = execute_tool(
            "edit",
            {"path": str(path), "old": "foo", "new": "bar", "replace_all": "false"},
            self.cwd,
        )
        self.assertIn("appears 2 times", out)
        self.assertEqual(path.read_text(encoding="utf-8"), "foo foo\n")

    def test_search_files_glob_does_not_cross_directories(self):
        self.write("ela/agent.py", "x\n")
        self.write("ela/tui/core.py", "x\n")
        out = tool_search({"pattern": "ela/*.py", "mode": "files"}, self.cwd)
        self.assertIn("ela/agent.py", out)
        self.assertNotIn("ela/tui/core.py", out)

    def test_search_files_globstar_matches_nested(self):
        self.write("a/b/c.py", "x\n")
        out = tool_search({"pattern": "**/*.py", "mode": "files"}, self.cwd)
        self.assertIn("a/b/c.py", out)

    def test_search_content_skips_nested_node_modules(self):
        self.write("a/node_modules/pkg/b.py", "TODO\n")
        self.write("a.py", "TODO\n")
        out = tool_search({"pattern": "TODO", "mode": "content"}, self.cwd)
        self.assertIn("a.py", out)
        self.assertNotIn("node_modules", out)

    def test_pwsh_cancellation_stops_the_command(self):
        started = time.perf_counter()
        stop_at = started + 0.3
        out = execute_tool(
            "pwsh",
            {"command": "Start-Sleep -Seconds 10", "timeout": 30},
            self.cwd,
            should_stop=lambda: time.perf_counter() >= stop_at,
        )
        self.assertIn("cancelled", out)
        self.assertLess(time.perf_counter() - started, 5)
    def test_unknown_tool(self):
        out = execute_tool("nope", {}, self.cwd)
        self.assertIn("unknown tool", out)


@unittest.skipUnless(os.name == "nt", "Windows executable discovery")
class PwshDiscoveryTests(unittest.TestCase):
    def test_prefers_preview_over_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            preview = root / "PowerShell" / "7-preview" / "pwsh.exe"
            stable = root / "PowerShell" / "7" / "pwsh.exe"
            preview.parent.mkdir(parents=True)
            stable.parent.mkdir(parents=True)
            preview.write_text("", encoding="utf-8")
            stable.write_text("", encoding="utf-8")
            env = {
                "ProgramFiles": str(root),
                "ProgramW6432": str(root),
                "LOCALAPPDATA": str(root / "local"),
                "ELA_PWSH": "",
            }
            with mock.patch.dict(os.environ, env), mock.patch(
                "ela.tools.shutil.which", return_value=None
            ):
                self.assertEqual(_find_pwsh(), str(preview))

    def test_falls_back_to_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stable = root / "PowerShell" / "7" / "pwsh.exe"
            stable.parent.mkdir(parents=True)
            stable.write_text("", encoding="utf-8")
            env = {
                "ProgramFiles": str(root),
                "ProgramW6432": str(root),
                "LOCALAPPDATA": str(root / "local"),
                "ELA_PWSH": "",
            }
            with mock.patch.dict(os.environ, env), mock.patch(
                "ela.tools.shutil.which", return_value=None
            ):
                self.assertEqual(_find_pwsh(), str(stable))

    def test_ela_pwsh_override_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            custom = Path(tmp) / "custom-pwsh.exe"
            custom.write_text("", encoding="utf-8")
            with mock.patch.dict(os.environ, {"ELA_PWSH": str(custom)}):
                self.assertEqual(_find_pwsh(), str(custom))


if __name__ == "__main__":
    unittest.main()
