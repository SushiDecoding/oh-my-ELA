from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

from ela.ui import MarkdownStream, PlainUI, sanitize


class MarkdownStreamTests(unittest.TestCase):
    def render(self, text: str) -> str:
        chunks: list[str] = []
        stream = MarkdownStream(chunks.append, enabled=False)
        stream.feed(text)
        stream.flush()
        return "".join(chunks)

    def test_headings_and_fences(self):
        out = self.render("# Title\n\nHello\n```python\nx = 1\n```\n")
        self.assertIn("Title\n", out)
        self.assertIn("Hello\n", out)
        self.assertIn("╭─ python\n", out)
        self.assertIn("│ x = 1\n", out)
        self.assertIn("╰─\n", out)

    def test_flush_closes_open_fence(self):
        out = self.render("```\nstill here")
        self.assertIn("│ still here\n", out)
        self.assertTrue(out.rstrip().endswith("╰─"))

    def test_long_line_streams_without_waiting_for_newline(self):
        stream_chunks: list[str] = []
        stream = MarkdownStream(stream_chunks.append, enabled=False)
        stream.feed("x" * 200)
        self.assertIn("x" * 200, "".join(stream_chunks))
        stream.feed("\n")
        stream.flush()
        self.assertTrue("".join(stream_chunks).endswith("\n"))


class SanitizeTests(unittest.TestCase):
    def test_strips_ansi_and_controls(self):
        self.assertEqual(sanitize("\x1b[31mred\x1b[0m"), "red")
        self.assertEqual(sanitize("a\rb"), "ab")
        self.assertEqual(sanitize("tab\there"), "tab\there")
        self.assertEqual(sanitize("bell\x07"), "bell")


class PlainUITests(unittest.TestCase):
    def test_no_double_newline(self):
        buffer = io.StringIO()
        ui = PlainUI()
        with redirect_stdout(buffer):
            ui.turn_start()
            ui.text("hello\n")
            ui.request_finish()
        self.assertEqual(buffer.getvalue(), "hello\n")

    def test_adds_missing_newline(self):
        buffer = io.StringIO()
        ui = PlainUI()
        with redirect_stdout(buffer):
            ui.turn_start()
            ui.text("hello")
            ui.request_finish()
        self.assertEqual(buffer.getvalue(), "hello\n")


if __name__ == "__main__":
    unittest.main()
