from __future__ import annotations

import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input import DummyInput
from prompt_toolkit.output import DummyOutput

from ela.fullscreen import FullScreenUI, TranscriptModel, inline_fragments
from ela.providers import PROVIDERS


def plain(fragments) -> str:
    return "".join(text for _, text in fragments)


class InlineFragmentTests(unittest.TestCase):
    def test_preserves_every_character(self):
        raw = "a **bold** and `code` and [link](http://example.com)"
        fragments = inline_fragments(raw)
        self.assertEqual(plain(fragments), raw)
        styles = {style for style, _ in fragments}
        self.assertIn("class:bold", styles)
        self.assertIn("class:code", styles)
        self.assertIn("class:link", styles)

    def test_empty(self):
        self.assertEqual(inline_fragments(""), [])


class TranscriptModelTests(unittest.TestCase):
    def test_streaming_and_code_fences(self):
        model = TranscriptModel()
        text = "hello\n```python\nx = 1\n```\nbye"
        self.assertEqual(model.feed(text), text)
        self.assertEqual(len(model.lines), 5)
        self.assertEqual(model.lines[0], [("class:assistant", "hello")])
        self.assertEqual(model.lines[1][0][0], "class:code.frame")
        self.assertEqual(model.lines[2], [("class:code", "x = 1")])
        self.assertEqual(model.lines[3][0][0], "class:code.frame")
        self.assertEqual(model.lines[4], [("class:assistant", "bye")])
        self.assertFalse(model.in_code)
        self.assertEqual(model.finish_request(), "\n")
        self.assertEqual(len(model.lines), 6)

    def test_append_line_after_partial_stream(self):
        model = TranscriptModel()
        model.feed("partial")
        appended = model.append_line([("class:info", "  hi")], "  hi", "info")
        self.assertEqual(appended, "\n  hi\n")
        self.assertEqual(len(model.lines), 3)
        self.assertEqual(model.lines[0], [("class:assistant", "partial")])
        self.assertEqual(model.lines[1], [("class:info", "  hi")])

    def test_blank_line_and_reset(self):
        model = TranscriptModel()
        self.assertEqual(model.append_blank(), "\n")
        self.assertEqual(len(model.lines), 2)
        model.reset()
        self.assertEqual(model.lines, [[]])
        self.assertFalse(model.in_code)

    def test_finish_without_partial_does_not_add_newline(self):
        model = TranscriptModel()
        model.feed("done\n")
        self.assertEqual(model.finish_request(), "")


class ScrollTests(unittest.TestCase):
    def test_page_scroll_moves_cursor_and_follows_bottom(self):
        ui = FullScreenUI(
            Path.cwd(),
            "test-model",
            messages=[],
            input=DummyInput(),
            output=DummyOutput(),
        )
        for index in range(100):
            ui._append_line([("class:info", f"line {index}")], f"line {index}", "info")
        ui._scroll_to_end()
        self.assertTrue(ui.follow)
        end_row = ui.transcript_buffer.document.cursor_position_row
        ui._term_size = lambda: (20, 80)
        ui._scroll_page(-1)
        self.assertFalse(ui.follow)
        self.assertLess(ui.transcript_buffer.document.cursor_position_row, end_row)
        ui._scroll_page(1)
        self.assertTrue(ui.follow)
        self.assertEqual(
            ui.transcript_buffer.document.cursor_position_row,
            ui.transcript_buffer.document.line_count - 1,
        )


class LoginFlowTests(unittest.TestCase):
    def make_ui(self) -> FullScreenUI:
        return FullScreenUI(
            Path.cwd(),
            "test-model",
            messages=[],
            input=DummyInput(),
            output=DummyOutput(),
        )

    def open(self, ui, on_submit=None):
        if on_submit is None:
            on_submit = lambda url, key: (["model-a", "model-b"], None)
        ui._open_login(on_submit, lambda model: None, "", "test-model")

    def apply_events(self, ui, timeout: float = 2.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with ui._event_lock:
                events, ui._events = ui._events, []
            if events:
                for name, args in events:
                    ui._apply(name, args)
                return
            time.sleep(0.01)
        self.fail("login worker produced no UI events")

    def provider_index(self, name: str) -> int:
        return next(
            index
            for index, provider in enumerate(PROVIDERS)
            if provider.id == name
        )

    def test_provider_stage_lists_endpoints(self):
        ui = self.make_ui()
        self.open(ui)
        self.assertTrue(ui.login_open)
        self.assertEqual(ui.login_stage, "provider")
        first_page = plain(ui._provider_fragments())
        self.assertIn("https://api.deepseek.com/v1", first_page)
        ui.provider_offset = self.provider_index("ollama")
        local_page = plain(ui._provider_fragments())
        self.assertIn("Ollama", local_page)
        self.assertIn("LM Studio", local_page)
        self.assertIn("llama.cpp", local_page)

    def test_choosing_local_provider_prefills_url_and_allows_no_key(self):
        ui = self.make_ui()
        self.open(ui)
        ui.provider_index = self.provider_index("lmstudio")
        ui._choose_provider()
        self.assertEqual(ui.login_stage, "credentials")
        self.assertEqual(
            ui.login_url_buffer.text, "http://127.0.0.1:1234/v1"
        )
        self.assertIn("optional", plain(ui._login_hint_fragments()))

    def test_cloud_provider_requires_key(self):
        ui = self.make_ui()
        self.open(ui)
        ui.provider_index = self.provider_index("openai")
        ui._choose_provider()
        ui.login_key_buffer.text = ""
        ui._submit_login()
        self.assertTrue(ui.login_open)
        self.assertIn("required", ui.login_error)

    def test_submit_closes_modal_and_lists_models(self):
        ui = self.make_ui()
        calls = []

        def submit(url, key):
            calls.append((url, key))
            return ["model-a", "model-b"], None

        self.open(ui, submit)
        ui.provider_index = self.provider_index("lmstudio")
        ui._choose_provider()
        ui.login_key_buffer.text = ""
        ui._submit_login()
        self.assertTrue(ui.login_open)
        self.assertTrue(ui.login_connecting)
        self.apply_events(ui)
        self.assertFalse(ui.login_open)
        self.assertFalse(ui.login_connecting)
        self.assertFalse(ui.busy)
        self.assertEqual(calls, [("http://127.0.0.1:1234/v1", "")])
        self.assertTrue(ui.model_picker_open)
        self.assertEqual(ui.model_picker_index, 0)

    def test_login_failure_keeps_modal_open(self):
        ui = self.make_ui()
        self.open(ui, lambda url, key: ([], "login failed: 401 bad key"))
        ui.provider_index = self.provider_index("lmstudio")
        ui._choose_provider()
        ui._submit_login()
        self.apply_events(ui)
        self.assertTrue(ui.login_open)
        self.assertIn("401", ui.login_error)
        self.assertFalse(ui.model_picker_open)

    def test_auto_picker_does_not_steal_focus_while_typing(self):
        ui = self.make_ui()
        self.open(ui)
        ui.input_buffer.text = "half-typed prompt"
        ui._on_login_result(["model-a"], "", lambda model: None, "test-model")
        self.assertFalse(ui.model_picker_open)



class InputHistoryTests(unittest.TestCase):
    def test_enter_records_input_history(self):
        # FileHistory would touch the real home directory; swap in memory.
        with mock.patch(
            "ela.tui.layout.FileHistory", lambda *args, **kwargs: InMemoryHistory()
        ):
            ui = FullScreenUI(
                Path.cwd(),
                "test-model",
                messages=[],
                input=DummyInput(),
                output=DummyOutput(),
            )
        ui._submit_text = lambda text: None
        binding = ui.input_control.key_bindings.get_bindings_for_keys(("c-m",))[0]
        ui.input_buffer.text = "remember me"
        event = SimpleNamespace(current_buffer=ui.input_buffer, app=ui.app, arg=None)
        binding.handler(event)
        self.assertEqual(ui.input_buffer.history.get_strings(), ["remember me"])
        self.assertEqual(ui.input_buffer.text, "")


if __name__ == "__main__":
    unittest.main()
