from __future__ import annotations

import ast
import unittest
from pathlib import Path

from prompt_toolkit.input import DummyInput
from prompt_toolkit.output import DummyOutput

import ela.fullscreen
import ela.tui
from ela.tui import askuser, core, goal, layout, login, modals, style, transcript

TUI_DIR = Path(ela.tui.__file__).parent


def make_ui():
    return ela.tui.FullScreenUI(
        Path.cwd(),
        "test-model",
        messages=[],
        input=DummyInput(),
        output=DummyOutput(),
    )


class ShimTests(unittest.TestCase):
    """ela.fullscreen must keep re-exporting the historical names."""

    def test_public_names_are_identical_objects(self):
        self.assertIs(ela.fullscreen.FullScreenUI, core.FullScreenUI)
        self.assertIs(ela.fullscreen.TranscriptModel, transcript.TranscriptModel)
        self.assertIs(ela.fullscreen.TranscriptLexer, transcript.TranscriptLexer)
        self.assertIs(ela.fullscreen.PermissionRequest, modals.PermissionRequest)
        self.assertIs(ela.fullscreen.inline_fragments, transcript.inline_fragments)

    def test_constants_are_identical(self):
        self.assertIs(ela.fullscreen.STYLE, style.STYLE)
        self.assertIs(ela.fullscreen.SPINNER, style.SPINNER)
        self.assertEqual(ela.fullscreen.PROVIDER_PAGE, style.PROVIDER_PAGE)
        self.assertEqual(ela.fullscreen.MODEL_PAGE, style.MODEL_PAGE)

    def test_all_declared_names_exist(self):
        for name in ela.fullscreen.__all__:
            self.assertTrue(hasattr(ela.fullscreen, name), name)


class MixinCompositionTests(unittest.TestCase):
    def test_fullscreen_inherits_every_mixin(self):
        bases = core.FullScreenUI.__mro__
        for mixin in (
            layout.LayoutMixin,
            transcript.TranscriptMixin,
            login.LoginMixin,
            askuser.AskUserMixin,
            modals.EffortPermissionMixin,
            goal.GoalMixin,
        ):
            self.assertIn(mixin, bases)

    def test_ui_subclass_contract_flags(self):
        ui = make_ui()
        self.assertTrue(ui.supports_permission_modal)
        self.assertTrue(ui.supports_login_modal)
        self.assertTrue(ui.supports_model_picker)
        self.assertFalse(ui.supports_modal_ask)

    def test_methods_resolve_across_modules(self):
        ui = make_ui()
        # one method per mixin, exercised through the assembled class
        self.assertTrue(callable(ui._build_layout))
        self.assertTrue(callable(ui._append_line))
        self.assertTrue(callable(ui._open_login))
        self.assertTrue(callable(ui._open_askuser))
        self.assertTrue(callable(ui._open_effort))
        self.assertTrue(callable(ui._goal_start_direct))


class NoMethodCollisionTests(unittest.TestCase):
    """Two mixins defining the same method silently shadows one of them."""

    def test_no_duplicate_method_names_across_modules(self):
        owners: dict[str, list[str]] = {}
        for path in sorted(TUI_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if not isinstance(node, ast.ClassDef):
                    continue
                for item in node.body:
                    if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef):
                        owners.setdefault(item.name, []).append(
                            f"{path.name}:{node.name}"
                        )
        duplicates = {
            name: places
            for name, places in owners.items()
            if len(places) > 1 and name != "__init__"
        }
        self.assertEqual(duplicates, {})


class ModuleSizeTests(unittest.TestCase):
    """The split exists so no single module is a maintenance hazard."""

    LIMIT = 600

    def test_no_module_exceeds_limit(self):
        oversized = {
            path.name: len(path.read_text(encoding="utf-8").splitlines())
            for path in sorted(TUI_DIR.glob("*.py"))
            if len(path.read_text(encoding="utf-8").splitlines()) > self.LIMIT
        }
        self.assertEqual(oversized, {})

    def test_fullscreen_shim_stays_small(self):
        shim = Path(ela.fullscreen.__file__)
        self.assertLessEqual(len(shim.read_text(encoding="utf-8").splitlines()), 60)


class TranscriptSplitTests(unittest.TestCase):
    def test_inline_fragments_live_in_transcript_module(self):
        self.assertIn("**", "".join(text for _, text in transcript.inline_fragments("a **b**")))

    def test_transcript_model_round_trip(self):
        model = transcript.TranscriptModel()
        self.assertEqual(model.feed("hi\n"), "hi\n")
        self.assertEqual(len(model.lines), 2)


class StyleTableTests(unittest.TestCase):
    def test_required_styles_present(self):
        for name in (
            "header",
            "footer",
            "modal.title",
            "modal.selected",
            "tool.ok",
            "tool.fail",
            "code.frame",
        ):
            self.assertIn(name, {rule[0] for rule in style.STYLE.style_rules})


if __name__ == "__main__":
    unittest.main()
