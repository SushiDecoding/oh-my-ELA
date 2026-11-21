"""Goal checklist rendering."""

from __future__ import annotations

from ..tools import one_line
from ..ui import duration, sanitize


class GoalMixin:
    """Goal checklist rendering."""

    def _goal_start_direct(self, goal: str) -> None:
        self.goal_total = 0
        self.goal_done = 0
        self.goal_status = ""
        text = "goal · " + one_line(sanitize(str(goal)), 100)
        self._append_line([("class:goal.title", text)], text, "goal")

    def _goal_plan_direct(self, goal: str, tasks) -> None:
        self.goal_total = len(tasks)
        self.goal_done = 0
        self.goal_status = ""
        title = f"  goal plan · {len(tasks)} tasks"
        self._append_line([("class:goal.title", title)], title, "goal")
        for task in tasks:
            line = f"  [ ] {task['id']}. {task['title']}"
            if len(line) > 110:
                line = line[:109] + "…"
            self._append_line([("class:goal.task", line)], line, "goal")

    def _goal_task_start_direct(self, index: int, total: int, title: str) -> None:
        self.goal_status = f"{index}/{total} {title}"
        line = "  ▸ " + one_line(f"{index}/{total} {title}", 100)
        self._append_line([("class:goal.meta", line)], line, "goal")
        if self.follow:
            self._scroll_to_end()

    def _goal_task_end_direct(
        self, index, total, title, ok, elapsed, summary
    ) -> None:
        self.goal_done += 1
        self.goal_status = ""
        mark = "x" if ok else "!"
        line = (
            f"  [{mark}] {index}. {one_line(str(title), 60)} · "
            f"{duration(elapsed)} · {one_line(str(summary), 100)}"
        )
        style = "class:goal.ok" if ok else "class:goal.fail"
        self._append_line([(style, line)], line, "goal")
        if self.follow:
            self._scroll_to_end()

    def _goal_finish_direct(self, ok: bool, completed: int, total: int) -> None:
        self.goal_total = 0
        self.goal_done = 0
        self.goal_status = ""
        line = (
            f"goal {'complete' if ok else 'incomplete'} · "
            f"{completed}/{total} tasks"
        )
        style = "class:goal.ok" if ok else "class:goal.fail"
        self._append_line([(style, "  " + line)], "  " + line, "goal")
        if self.follow:
            self._scroll_to_end()
