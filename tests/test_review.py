from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ela.config import Config
from ela.review import _parse_review, review_action


def response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


class ReviewParseTests(unittest.TestCase):
    def test_safe_true(self):
        ok, reason = _parse_review('{"safe": true, "reason": "workspace edit"}')
        self.assertTrue(ok)
        self.assertEqual(reason, "workspace edit")

    def test_safe_false(self):
        ok, reason = _parse_review(
            '```json\n{"safe": false, "reason": "deletes files"}\n```'
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "deletes files")

    def test_string_boolean(self):
        ok, _ = _parse_review('{"safe": "yes", "reason": "ok"}')
        self.assertTrue(ok)

    def test_invalid_json_is_unsafe(self):
        ok, reason = _parse_review("I think it is fine")
        self.assertFalse(ok)
        self.assertIn("invalid JSON", reason)


class ReviewActionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cwd = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_reviewer_returns_json(self):
        class Completions:
            def create(self, **kwargs):
                return response('{"safe": true, "reason": "safe build"}')

        client = SimpleNamespace(
            chat=SimpleNamespace(completions=Completions())
        )
        ok, reason = review_action(
            client, Config(), self.cwd, "pwsh", {}, "pytest", "PS> pytest"
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "safe build")

    def test_falls_back_without_json_mode(self):
        class Completions:
            def __init__(self):
                self.calls = 0

            def create(self, **kwargs):
                self.calls += 1
                if "response_format" in kwargs:
                    raise RuntimeError("response_format not supported")
                return response('{"safe": false, "reason": "needs approval"}')

        client = SimpleNamespace(
            chat=SimpleNamespace(completions=Completions())
        )
        ok, reason = review_action(
            client, Config(), self.cwd, "write", {}, "a.txt", "content"
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "needs approval")

    def test_api_failure_is_unsafe(self):
        class Completions:
            def create(self, **kwargs):
                raise RuntimeError("down")

        client = SimpleNamespace(
            chat=SimpleNamespace(completions=Completions())
        )
        ok, reason = review_action(
            client, Config(), self.cwd, "pwsh", {}, "x", "x"
        )
        self.assertFalse(ok)
        self.assertIn("reviewer unavailable", reason)


if __name__ == "__main__":
    unittest.main()
