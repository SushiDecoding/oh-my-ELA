from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ela.permissions import (
    PermissionManager,
    PermissionMode,
    next_mode,
    parse_mode,
)


class FakeUI:
    def __init__(self, allow: bool = True):
        self.allow = allow
        self.calls: list[tuple[str, str, str]] = []
        self.infos: list[str] = []

    def approve(self, tool: str, summary: str, detail: str) -> bool:
        self.calls.append((tool, summary, detail))
        return self.allow

    def request_start(self, label: str = "thinking") -> None:
        pass

    def request_finish(self) -> None:
        pass

    def info(self, message: str) -> None:
        self.infos.append(message)


class PermissionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cwd = Path(self._tmp.name)
        self.inside = self.cwd / "a.txt"
        self.outside = str(self.cwd.parent / "outside.txt")

    def tearDown(self):
        self._tmp.cleanup()

    def test_readonly_allows_reads_everywhere(self):
        manager = PermissionManager(self.cwd, PermissionMode.READONLY)
        ui = FakeUI()
        allowed, _ = manager.authorize(
            "read", {"paths": [self.outside]}, "", "", ui
        )
        self.assertTrue(allowed)
        allowed, _ = manager.authorize(
            "search", {"pattern": "x", "path": self.outside}, "", "", ui
        )
        self.assertTrue(allowed)
        for tool, args in (
            ("write", {"path": "a.txt"}),
            ("edit", {"path": "a.txt"}),
            ("pwsh", {"command": "Write-Output hi"}),
        ):
            allowed, error = manager.authorize(tool, args, "", "", ui)
            self.assertFalse(allowed)
            self.assertIn("Readonly Mode", error)
        self.assertEqual(ui.calls, [])

    def test_ask_confines_reads_to_workspace(self):
        manager = PermissionManager(self.cwd, PermissionMode.ASK)
        ui = FakeUI()
        allowed, _ = manager.authorize(
            "read", {"paths": [str(self.inside)]}, "", "", ui
        )
        self.assertTrue(allowed)
        allowed, error = manager.authorize(
            "read", {"paths": [self.outside]}, "", "", ui
        )
        self.assertFalse(allowed)
        self.assertIn("outside the workspace", error)
        allowed, error = manager.authorize(
            "search", {"pattern": "x", "path": self.outside}, "", "", ui
        )
        self.assertFalse(allowed)
        self.assertEqual(ui.calls, [])

    def test_ask_prompts_for_writes_and_pwsh(self):
        manager = PermissionManager(self.cwd, PermissionMode.ASK)
        ui = FakeUI(allow=True)
        allowed, _ = manager.authorize(
            "write", {"path": "a.txt"}, "a.txt", "content", ui
        )
        self.assertTrue(allowed)
        self.assertEqual(ui.calls[0][0], "write")
        allowed, _ = manager.authorize(
            "pwsh", {"command": "Write-Output hi"}, "Write-Output hi", "Write-Output hi", ui
        )
        self.assertTrue(allowed)
        self.assertIn("not filesystem-sandboxed", ui.calls[1][2])

        deny_ui = FakeUI(allow=False)
        allowed, error = manager.authorize(
            "write", {"path": "a.txt"}, "a.txt", "content", deny_ui
        )
        self.assertFalse(allowed)
        self.assertIn("denied by user", error)

    def test_ask_denies_outside_write_without_prompt(self):
        manager = PermissionManager(self.cwd, PermissionMode.ASK)
        ui = FakeUI(allow=True)
        allowed, error = manager.authorize(
            "write", {"path": self.outside}, "", "", ui
        )
        self.assertFalse(allowed)
        self.assertIn("outside the workspace", error)
        self.assertEqual(ui.calls, [])

    def test_full_allows_everything_without_prompt(self):
        manager = PermissionManager(self.cwd, PermissionMode.FULL)
        ui = FakeUI(allow=False)
        for tool, args in (
            ("write", {"path": self.outside}),
            ("edit", {"path": self.outside}),
            ("pwsh", {"command": "Remove-Item -Recurse C:\\"}),
            ("read", {"paths": [self.outside]}),
            ("task", {"description": "x", "prompt": "y"}),
        ):
            allowed, error = manager.authorize(tool, args, "", "", ui)
            self.assertTrue(allowed, (tool, error))
        self.assertEqual(ui.calls, [])

    def test_task_is_allowed_in_every_mode(self):
        for mode in PermissionMode:
            manager = PermissionManager(self.cwd, mode)
            allowed, _ = manager.authorize(
                "task", {"description": "x", "prompt": "y"}, "", "", FakeUI()
            )
            self.assertTrue(allowed)

    def test_mode_parsing_and_cycle(self):
        self.assertEqual(parse_mode("read-only"), PermissionMode.READONLY)
        self.assertEqual(parse_mode("YOLO"), PermissionMode.FULL)
        self.assertEqual(parse_mode("auto"), PermissionMode.FULL)
        self.assertEqual(
            parse_mode("AutoReview Mode"), PermissionMode.AUTOREVIEW
        )
        self.assertEqual(next_mode(PermissionMode.ASK), PermissionMode.READONLY)
        self.assertEqual(
            next_mode(PermissionMode.READONLY), PermissionMode.AUTOREVIEW
        )
        self.assertEqual(next_mode(PermissionMode.AUTOREVIEW), PermissionMode.FULL)
        manager = PermissionManager(self.cwd, PermissionMode.ASK)
        self.assertEqual(manager.cycle(), PermissionMode.READONLY)
        self.assertEqual(manager.cycle(), PermissionMode.AUTOREVIEW)
        self.assertEqual(manager.cycle(), PermissionMode.FULL)
        self.assertEqual(manager.cycle(), PermissionMode.ASK)

    def test_autoreview_safe_skips_user_approval(self):
        manager = PermissionManager(self.cwd, PermissionMode.AUTOREVIEW)
        ui = FakeUI(allow=False)
        reviewed = []

        def reviewer(tool, args, summary, detail):
            reviewed.append(tool)
            return True, "ordinary workspace edit"

        allowed, error = manager.authorize(
            "write",
            {"path": "a.txt"},
            "a.txt",
            "content",
            ui,
            reviewer=reviewer,
        )
        self.assertTrue(allowed, error)
        self.assertEqual(reviewed, ["write"])
        self.assertEqual(ui.calls, [])
        self.assertTrue(any("auto-review" in line for line in ui.infos))

    def test_autoreview_unsafe_asks_user(self):
        manager = PermissionManager(self.cwd, PermissionMode.AUTOREVIEW)
        ui = FakeUI(allow=True)

        def reviewer(tool, args, summary, detail):
            return False, "deletes files outside the workspace"

        allowed, error = manager.authorize(
            "pwsh",
            {"command": "Remove-Item -Recurse ..\\x"},
            "Remove-Item -Recurse ..\\x",
            "PS> Remove-Item -Recurse ..\\x",
            ui,
            reviewer=reviewer,
        )
        self.assertTrue(allowed, error)
        self.assertEqual(len(ui.calls), 1)
        self.assertIn("auto-review", ui.calls[0][2])
        self.assertIn("deletes files", ui.calls[0][2])

        deny_ui = FakeUI(allow=False)
        allowed, error = manager.authorize(
            "pwsh",
            {"command": "Remove-Item -Recurse ..\\x"},
            "Remove-Item -Recurse ..\\x",
            "PS> Remove-Item -Recurse ..\\x",
            deny_ui,
            reviewer=reviewer,
        )
        self.assertFalse(allowed)
        self.assertIn("denied by user", error)

    def test_autoreview_reviewer_failure_asks_user(self):
        manager = PermissionManager(self.cwd, PermissionMode.AUTOREVIEW)
        ui = FakeUI(allow=True)

        def reviewer(tool, args, summary, detail):
            raise RuntimeError("review model down")

        allowed, error = manager.authorize(
            "write", {"path": "a.txt"}, "a.txt", "content", ui, reviewer=reviewer
        )
        self.assertTrue(allowed, error)
        self.assertEqual(len(ui.calls), 1)
        self.assertIn("review unavailable", ui.calls[0][2])

    def test_autoreview_without_reviewer_asks_user(self):
        manager = PermissionManager(self.cwd, PermissionMode.AUTOREVIEW)
        ui = FakeUI(allow=True)
        allowed, error = manager.authorize(
            "write", {"path": "a.txt"}, "a.txt", "content", ui, reviewer=None
        )
        self.assertTrue(allowed, error)
        self.assertEqual(len(ui.calls), 1)

    def test_autoreview_confines_reads(self):
        manager = PermissionManager(self.cwd, PermissionMode.AUTOREVIEW)
        ui = FakeUI(allow=True)
        allowed, _ = manager.authorize(
            "read", {"paths": [self.outside]}, "", "", ui, reviewer=None
        )
        self.assertFalse(allowed)
        self.assertEqual(ui.calls, [])


if __name__ == "__main__":
    unittest.main()
