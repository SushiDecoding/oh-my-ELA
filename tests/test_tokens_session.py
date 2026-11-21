from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ela.session import Session, latest_session, list_sessions, open_session
from ela.tokens import estimate_tokens, rough_tokens, trim_messages


class TokenTests(unittest.TestCase):
    def test_trim_keeps_system_and_recent_turns(self):
        messages = [{"role": "system", "content": "system"}]
        for index in range(8):
            messages.append({"role": "user", "content": "u" * 400})
            messages.append({"role": "assistant", "content": "a" * 400})
            messages.append(
                {"role": "tool", "tool_call_id": f"c{index}", "content": "t" * 400}
            )
        trimmed, dropped = trim_messages(messages, 600)
        self.assertGreater(dropped, 0)
        self.assertEqual(trimmed[0]["role"], "system")
        self.assertEqual(trimmed[1]["role"], "user")
        self.assertLess(estimate_tokens(trimmed), estimate_tokens(messages))

    def test_trim_leaves_small_history_alone(self):
        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        trimmed, dropped = trim_messages(messages, 1000)
        self.assertEqual(dropped, 0)
        self.assertEqual(trimmed, messages)

    def test_cjk_is_not_underestimated(self):
        self.assertGreater(rough_tokens("你好世界"), rough_tokens("abcd"))


class SessionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.cwd = self.home / "project"
        self.cwd.mkdir()
        self._patch = mock.patch("ela.session.SESSIONS_DIR", self.home / "sessions")
        self._patch.start()
        (self.home / "sessions").mkdir()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_roundtrip(self):
        session = Session(self.cwd, model="m")
        session.append({"role": "user", "content": "first request"})
        session.append({"role": "assistant", "content": "answer"})
        infos = list_sessions(self.cwd)
        self.assertEqual(len(infos), 1)
        self.assertEqual(infos[0].messages, 2)
        self.assertIn("first request", infos[0].snippet)

        reopened = open_session(latest_session(self.cwd))
        messages = reopened.load()
        self.assertEqual(messages[0]["content"], "first request")
        self.assertEqual(messages[1]["content"], "answer")

    def test_closed_cwd_is_not_listed(self):
        other = self.home / "other"
        other.mkdir()
        Session(other, model="m").append({"role": "user", "content": "elsewhere"})
        self.assertEqual(list_sessions(self.cwd), [])

    def test_rewrite_replaces_transcript(self):
        session = Session(self.cwd, model="m")
        session.append({"role": "user", "content": "old"})
        session.rewrite([{"role": "user", "content": "new"}])
        self.assertEqual(session.load()[0]["content"], "new")

    def test_rewrite_skips_system_prompt(self):
        session = Session(self.cwd, model="m")
        session.rewrite(
            [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hi"},
            ]
        )
        loaded = session.load()
        self.assertEqual([message["role"] for message in loaded], ["user"])
        self.assertEqual(loaded[0]["content"], "hi")

    def test_update_model_rewrites_meta(self):
        session = Session(self.cwd, model="old-model")
        session.append({"role": "user", "content": "hi"})
        session.update_model("new-model")
        info = latest_session(self.cwd)
        self.assertEqual(info.model, "new-model")
        self.assertEqual(open_session(info).load()[0]["content"], "hi")


if __name__ == "__main__":
    unittest.main()
