from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from ela.agent import Agent
from ela.config import Config
from ela.permissions import PermissionManager, PermissionMode
from ela.session import NullSession
from ela.ui import NullUI


def delta(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def chunk(delta_obj=None, finish=None, usage=None):
    choices = []
    if delta_obj is not None:
        choices.append(SimpleNamespace(delta=delta_obj, finish_reason=finish))
    return SimpleNamespace(choices=choices, usage=usage)


class FakeCompletions:
    def __init__(self, streams):
        self.streams = list(streams)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return iter(self.streams.pop(0))


class CaptureUI(NullUI):
    def __init__(self):
        self.warns: list[str] = []
        self.infos: list[str] = []

    def warn(self, message: str) -> None:
        self.warns.append(str(message))

    def info(self, message: str) -> None:
        self.infos.append(str(message))


class AgentLoopTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cwd = Path(self._tmp.name)
        self.config = Config(model="fake-model", context_window=128_000, max_steps=5)

    def tearDown(self):
        self._tmp.cleanup()

    def make_agent(self, streams, ui=None):
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions(streams))
        )
        return Agent(
            self.config,
            self.cwd,
            ui if ui is not None else NullUI(),
            NullSession(self.cwd),
            client=client,
            permissions=PermissionManager(self.cwd, PermissionMode.FULL),
        )

    def test_tool_call_then_answer(self):
        tool_delta = SimpleNamespace(
            index=0,
            id="call_abc",
            function=SimpleNamespace(
                name="write",
                arguments='{"path": "out.txt", "content": "hi\\n"}',
            ),
        )
        first = [
            chunk(delta(tool_calls=[tool_delta]), finish="tool_calls"),
            chunk(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5)),
        ]
        second = [
            chunk(delta("do")),
            chunk(delta("ne"), finish="stop"),
            chunk(usage=SimpleNamespace(prompt_tokens=12, completion_tokens=2)),
        ]
        agent = self.make_agent([first, second])
        result = agent.run("write a file")

        self.assertEqual(result, "done")
        self.assertFalse(agent.last_failed)
        self.assertEqual((self.cwd / "out.txt").read_text(encoding="utf-8"), "hi\n")

        roles = [message["role"] for message in agent.messages]
        self.assertEqual(roles, ["system", "user", "assistant", "tool", "assistant"])
        call = agent.messages[2]["tool_calls"][0]
        self.assertEqual(call["function"]["name"], "write")
        self.assertEqual(agent.messages[3]["tool_call_id"], "call_abc")
        self.assertIn("Wrote", agent.messages[3]["content"])
        self.assertEqual(agent.total_usage.prompt_tokens, 22)
        self.assertEqual(agent.total_usage.completion_tokens, 7)

    def test_invalid_tool_arguments_become_error_value(self):
        tool_delta = SimpleNamespace(
            index=0,
            id="call_bad",
            function=SimpleNamespace(name="read", arguments="{not json"),
        )
        first = [
            chunk(delta(tool_calls=[tool_delta]), finish="tool_calls"),
            chunk(usage=SimpleNamespace(prompt_tokens=8, completion_tokens=3)),
        ]
        second = [
            chunk(delta("ok"), finish="stop"),
            chunk(usage=SimpleNamespace(prompt_tokens=9, completion_tokens=1)),
        ]
        agent = self.make_agent([first, second])
        agent.run("try")
        tool_message = next(m for m in agent.messages if m["role"] == "tool")
        self.assertTrue(tool_message["content"].startswith("Error:"))

    def test_no_content_and_no_tools_is_handled(self):
        stream = [
            chunk(delta(None), finish="stop"),
            chunk(usage=SimpleNamespace(prompt_tokens=4, completion_tokens=0)),
        ]
        agent = self.make_agent([stream])
        result = agent.run("hello")
        self.assertEqual(result, "")
        self.assertEqual([m["role"] for m in agent.messages], ["system", "user"])

    def test_askuser_returns_selected_option(self):
        agent = self.make_agent([])
        seen = {}

        class AskUI:
            def tool_start(self, name, summary):
                pass

            def tool_end(self, name, summary, result, elapsed, ok):
                seen["result"] = result

            def ask_user(self, question, options):
                seen["question"] = question
                seen["options"] = options
                return options[1]

        agent.ui = AskUI()
        call = {
            "id": "a1",
            "function": {
                "name": "askuser",
                "arguments": json.dumps(
                    {"question": "Which file?", "options": ["a.py", "b.py"]}
                ),
            },
        }
        message = agent._execute_call(call)
        self.assertEqual(seen["question"], "Which file?")
        self.assertEqual(seen["options"], ["a.py", "b.py"])
        self.assertIn("User answered: b.py", message["content"])
        self.assertIn("User answered: b.py", seen["result"])

    def test_askuser_rejects_invalid_options(self):
        agent = self.make_agent([])
        asked = []

        class AskUI:
            def tool_start(self, name, summary):
                pass

            def tool_end(self, name, summary, result, elapsed, ok):
                pass

            def ask_user(self, question, options):
                asked.append(question)
                return options[0]

        agent.ui = AskUI()
        call = {
            "id": "a2",
            "function": {
                "name": "askuser",
                "arguments": json.dumps(
                    {"question": "Only one?", "options": ["only"]}
                ),
            },
        }
        message = agent._execute_call(call)
        self.assertIn("Error:", message["content"])
        self.assertEqual(asked, [])

    def test_ultracode_adds_task_tool_and_prompt(self):
        agent = self.make_agent([])
        names = {tool["function"]["name"] for tool in agent.tools}
        self.assertNotIn("task", names)
        agent.set_ultracode(True)
        names = {tool["function"]["name"] for tool in agent.tools}
        self.assertIn("task", names)
        self.assertIn("Ultracode", agent.system_prompt)
        agent.set_effort("high")
        self.assertIn("Effort:", agent.system_prompt)
        agent.set_ultracode(False)
        names = {tool["function"]["name"] for tool in agent.tools}
        self.assertNotIn("task", names)

    def test_subagents_can_fan_out_with_depth_limit(self):
        agent = self.make_agent([])
        agent.set_ultracode(True)
        child = agent._make_subagent("child")
        grandchild = child._make_subagent("grandchild")

        child_names = {tool["function"]["name"] for tool in child.tools}
        grandchild_names = {
            tool["function"]["name"] for tool in grandchild.tools
        }
        self.assertIn("task", child_names)
        self.assertNotIn("askuser", child_names)
        self.assertEqual(child.subagent_depth, 1)
        self.assertTrue(child.ultracode)
        self.assertIn("fan out", child.system_prompt)

        self.assertNotIn("task", grandchild_names)
        self.assertEqual(grandchild.subagent_depth, 2)
        self.assertFalse(grandchild.ultracode)
        self.assertNotIn("fan out", grandchild.system_prompt)

    def test_run_task_wraps_child_answer(self):
        agent = self.make_agent([])

        class FakeChild:
            last_failed = False
            ui = SimpleNamespace(errors=[])

            def run(self, text):
                return "child answer"

        agent._make_subagent = lambda description: FakeChild()
        call = {
            "id": "t1",
            "function": {
                "name": "task",
                "arguments": json.dumps(
                    {"description": "research", "prompt": "find x"}
                ),
            },
        }
        content, ok, elapsed, description = agent._run_task(call)
        self.assertTrue(ok)
        self.assertEqual(description, "research")
        self.assertIn("[subagent: research]", content)
        self.assertIn("child answer", content)

    def test_parallel_tasks_keep_result_order(self):
        agent = self.make_agent([])
        agent.set_ultracode(True)

        def fake_run(call):
            args = json.loads(call["function"]["arguments"])
            time.sleep(0.05)
            return (
                f"[subagent: {args['description']}]\nanswer {args['description']}",
                True,
                0.05,
                args["description"],
            )

        agent._run_task = fake_run
        calls = [
            {
                "id": "t1",
                "function": {
                    "name": "task",
                    "arguments": json.dumps(
                        {"description": "A", "prompt": "do A"}
                    ),
                },
            },
            {
                "id": "t2",
                "function": {
                    "name": "task",
                    "arguments": json.dumps(
                        {"description": "B", "prompt": "do B"}
                    ),
                },
            },
        ]
        agent._execute_tool_calls(calls)
        tool_messages = [m for m in agent.messages if m["role"] == "tool"]
        self.assertEqual([m["tool_call_id"] for m in tool_messages], ["t1", "t2"])
        self.assertIn("answer A", tool_messages[0]["content"])
        self.assertIn("answer B", tool_messages[1]["content"])

    def test_loading_interrupted_transcript_repairs_tool_pair(self):
        agent = self.make_agent([])
        agent.load_messages(
            [
                {"role": "user", "content": "earlier request"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_orphan",
                            "type": "function",
                            "function": {"name": "read", "arguments": "{}"},
                        }
                    ],
                },
            ]
        )
        self.assertEqual(agent.messages[-1]["role"], "tool")
        self.assertEqual(agent.messages[-1]["tool_call_id"], "call_orphan")
        self.assertIn("interrupted", agent.messages[-1]["content"])

    def test_step_cap_warns_and_stops(self):
        self.config.max_steps = 2

        def loop_stream(index):
            call = SimpleNamespace(
                index=0,
                id=f"call_{index}",
                function=SimpleNamespace(
                    name="write",
                    arguments='{"path": "out.txt", "content": "x"}',
                ),
            )
            return [
                chunk(delta(tool_calls=[call]), finish="tool_calls"),
                chunk(usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1)),
            ]

        ui = CaptureUI()
        agent = self.make_agent([loop_stream(1), loop_stream(2)], ui=ui)
        agent.run("loop forever")
        self.assertIn("stopped after 2 steps", " ".join(ui.warns))

    def test_unlimited_steps_finishes_without_warning(self):
        self.config.max_steps = 0
        stream = [
            chunk(delta("ok"), finish="stop"),
            chunk(usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1)),
        ]
        ui = CaptureUI()
        agent = self.make_agent([stream], ui=ui)
        self.assertEqual(agent.run("hi"), "ok")
        self.assertEqual(ui.warns, [])

    def test_subagent_cap_survives_unlimited_parent(self):
        self.config.max_steps = 0
        agent = self.make_agent([])
        child = agent._make_subagent("child")
        self.assertEqual(child.config.max_steps, 20)


    def test_cancel_stops_a_running_pwsh_tool(self):
        agent = self.make_agent([])
        threading.Timer(0.2, agent.cancel).start()
        call = {
            "id": "c1",
            "function": {
                "name": "pwsh",
                "arguments": json.dumps(
                    {"command": "Start-Sleep -Seconds 10", "timeout": 30}
                ),
            },
        }
        started = time.perf_counter()
        message = agent._execute_call(call)
        self.assertIn("cancelled", message["content"])
        self.assertLess(time.perf_counter() - started, 5)
    def test_context_percent_survives_invalid_config_value(self):
        self.config.context_window = "abc"
        agent = self.make_agent([])
        self.assertIsInstance(agent.context_percent, int)
        agent._prepare_context()

    def test_compact_keeps_history_when_summary_does_not_reduce(self):
        summary = "a summary that is longer than the tiny history " * 4

        class SummaryCompletions:
            def create(self, **kwargs):
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(message=SimpleNamespace(content=summary))
                    ]
                )

        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SummaryCompletions())
        )
        agent = Agent(
            self.config,
            self.cwd,
            NullUI(),
            NullSession(self.cwd),
            client=client,
            permissions=PermissionManager(self.cwd, PermissionMode.FULL),
        )
        agent.messages.append({"role": "user", "content": "hi"})
        agent.messages.append({"role": "assistant", "content": "ok"})
        before = list(agent.messages)
        self.assertFalse(agent.compact())
        self.assertEqual(agent.messages, before)



if __name__ == "__main__":
    unittest.main()
