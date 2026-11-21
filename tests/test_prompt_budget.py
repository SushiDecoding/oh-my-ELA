from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ela.budget import BASE_BUDGET, MAX_BUDGET, breakdown
from ela.prompt import (
    MAX_CONTEXT_CHARS,
    TOOLS,
    build_system_prompt,
    get_tools,
    read_project_context,
)


class PromptBudgetTests(unittest.TestCase):
    def test_base_prompt_within_budget(self):
        data = breakdown(Path.cwd())
        self.assertLessEqual(
            data["base"],
            BASE_BUDGET,
            f"base prompt budget exceeded: {data}",
        )
        self.assertGreater(data["base_system"], 0)
        self.assertGreater(data["base_tools"], 0)

    def test_max_variant_within_budget(self):
        data = breakdown(Path.cwd())
        self.assertLessEqual(
            data["max"],
            MAX_BUDGET,
            f"max prompt budget exceeded: {data}",
        )
        self.assertGreaterEqual(data["max_system"], data["base_system"])
        self.assertGreaterEqual(data["max_tools"], data["base_tools"])

    def test_budgets_keep_headroom(self):
        data = breakdown(Path.cwd())
        self.assertGreaterEqual(data["base_left"], 50, f"base too tight: {data}")
        self.assertGreaterEqual(data["max_left"], 50, f"max too tight: {data}")

    def test_max_budget_is_the_hard_ceiling(self):
        self.assertEqual(MAX_BUDGET, 1500)
        self.assertLess(BASE_BUDGET, MAX_BUDGET)

    def test_project_context_is_separate(self):
        base = build_system_prompt(Path.cwd(), context="")
        with_context = build_system_prompt(Path.cwd(), context="\n\nProject instructions:\nhello")
        self.assertLess(len(base), len(with_context))

    def test_all_tools_have_required_fields(self):
        names = set()
        for tool in TOOLS:
            self.assertEqual(tool["type"], "function")
            function = tool["function"]
            self.assertTrue(function["name"])
            self.assertTrue(function["description"])
            self.assertIn("parameters", function)
            names.add(function["name"])
        self.assertEqual(
            names, {"read", "write", "edit", "pwsh", "search", "askuser"}
        )

    def test_ultracode_adds_task_and_prompt_lines(self):
        names = {tool["function"]["name"] for tool in get_tools(True)}
        self.assertEqual(
            names,
            {"read", "write", "edit", "pwsh", "search", "askuser", "task"},
        )
        prompt = build_system_prompt(
            Path.cwd(),
            context="",
            effort="high",
            ultracode=True,
            permission="ask",
        )
        self.assertIn("Ultracode", prompt)
        self.assertIn("Permissions:", prompt)
        self.assertIn("Effort:", prompt)

    def test_readonly_hides_write_tools(self):
        names = {
            tool["function"]["name"] for tool in get_tools(False, "readonly")
        }
        self.assertEqual(names, {"read", "search", "askuser"})
        names = {
            tool["function"]["name"] for tool in get_tools(True, "readonly")
        }
        self.assertEqual(names, {"read", "search", "askuser", "task"})

    def test_search_pattern_documents_regex_and_glob(self):
        search = next(
            tool for tool in TOOLS if tool["function"]["name"] == "search"
        )
        pattern = search["function"]["parameters"]["properties"]["pattern"]
        self.assertIn("Regex", pattern["description"])
        self.assertIn("glob", pattern["description"])

    def test_read_does_not_advertise_directories(self):
        read = next(
            tool for tool in TOOLS if tool["function"]["name"] == "read"
        )
        description = read["function"]["description"]
        self.assertNotIn("Files or directories", description)
        self.assertIn("search mode=files", description)

    def test_askuser_options_are_bounded(self):
        askuser = next(
            tool for tool in TOOLS if tool["function"]["name"] == "askuser"
        )
        options = askuser["function"]["parameters"]["properties"]["options"]
        self.assertEqual(options["minItems"], 2)
        self.assertEqual(options["maxItems"], 4)

    def test_prompt_forbids_credential_leaks(self):
        prompt = build_system_prompt(Path.cwd(), context="")
        self.assertIn("Never print API keys", prompt)

    def test_medium_effort_does_not_force_planning(self):
        prompt = build_system_prompt(
            Path.cwd(), context="", effort="medium"
        )
        self.assertNotIn("plan briefly", prompt)


class ProjectContextTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cwd = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_both_files_are_merged(self):
        (self.cwd / "ELA.md").write_text("ela rules", encoding="utf-8")
        (self.cwd / "AGENTS.md").write_text("agents rules", encoding="utf-8")
        context = read_project_context(self.cwd)
        self.assertIn("ela rules", context)
        self.assertIn("agents rules", context)

    def test_shared_cap_is_enforced(self):
        (self.cwd / "ELA.md").write_text("a" * MAX_CONTEXT_CHARS, encoding="utf-8")
        (self.cwd / "AGENTS.md").write_text("b" * 500, encoding="utf-8")
        context = read_project_context(self.cwd)
        body = context.split("AGENTS.md):\n", 1)[-1] if "AGENTS.md" in context else context
        self.assertNotIn("b" * 500, body)
        self.assertIn("truncated", context)

    def test_missing_files_yield_empty_string(self):
        self.assertEqual(read_project_context(self.cwd), "")


if __name__ == "__main__":
    unittest.main()
