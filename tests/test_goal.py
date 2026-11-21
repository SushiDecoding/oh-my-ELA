from __future__ import annotations

import unittest

from ela.goal import parse_plan, parse_task_status, plan_waves, summarize_reply


class GoalPlanTests(unittest.TestCase):
    def test_parse_json(self):
        text = (
            '{"tasks":['
            '{"id":1,"title":"Read code","detail":"Inspect","depends_on":[]},'
            '{"id":2,"title":"Edit code","detail":"Change","depends_on":[1]}'
            "]}"
        )
        tasks = parse_plan(text)
        self.assertEqual([task["id"] for task in tasks], [1, 2])
        self.assertEqual(tasks[1]["depends_on"], [1])
        self.assertEqual(tasks[0]["title"], "Read code")

    def test_parse_fenced_json_and_assign_ids(self):
        text = """```json
{"tasks": ["first", "second"]}
```"""
        tasks = parse_plan(text)
        self.assertEqual([task["title"] for task in tasks], ["first", "second"])
        self.assertEqual([task["id"] for task in tasks], [1, 2])

    def test_parse_bullets_when_not_json(self):
        text = "- [ ] inspect\n2. implement\n"
        tasks = parse_plan(text)
        self.assertEqual([task["title"] for task in tasks], ["inspect", "implement"])

    def test_empty_plan(self):
        self.assertEqual(parse_plan("no tasks here"), [])

    def test_plan_waves_orders_dependencies(self):
        tasks = parse_plan(
            '{"tasks":['
            '{"id":1,"title":"a","depends_on":[]},'
            '{"id":2,"title":"b","depends_on":[1]},'
            '{"id":3,"title":"c","depends_on":[]}'
            "]}"
        )
        waves = plan_waves(tasks)
        self.assertEqual(len(waves), 2)
        first_ids = {task["id"] for task in waves[0]}
        self.assertEqual(first_ids, {1, 3})
        self.assertEqual([task["id"] for task in waves[1]], [2])

    def test_plan_waves_breaks_cycle(self):
        tasks = parse_plan(
            '{"tasks":['
            '{"id":1,"title":"a","depends_on":[2]},'
            '{"id":2,"title":"b","depends_on":[1]}'
            "]}"
        )
        waves = plan_waves(tasks)
        self.assertEqual(len(waves), 2)


class GoalStatusTests(unittest.TestCase):
    def test_status_done(self):
        ok, summary = parse_task_status("all good\nTASK STATUS: done")
        self.assertTrue(ok)
        self.assertEqual(summary, "")

    def test_status_failed(self):
        ok, summary = parse_task_status(
            "TASK STATUS: failed: tests still fail"
        )
        self.assertFalse(ok)
        self.assertEqual(summary, "tests still fail")

    def test_status_missing_is_ok_with_summary(self):
        ok, summary = parse_task_status("implemented the parser")
        self.assertTrue(ok)
        self.assertEqual(summary, "implemented the parser")

    def test_summarize_strips_status_line(self):
        summary = summarize_reply("did work\nTASK STATUS: done")
        self.assertEqual(summary, "did work")


if __name__ == "__main__":
    unittest.main()
