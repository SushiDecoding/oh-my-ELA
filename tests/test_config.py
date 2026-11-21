from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ela.config import Config, load_config


class LoadConfigTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "config.json"
        self._patch = mock.patch("ela.config.CONFIG_PATH", self.path)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def write(self, data) -> None:
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def test_defaults_when_file_is_missing(self):
        self.assertEqual(load_config(), Config())

    def test_invalid_numbers_fall_back_to_defaults(self):
        self.write({"context_window": "abc", "max_steps": "abc"})
        with mock.patch("sys.stderr"):
            config = load_config()
        defaults = Config()
        self.assertEqual(config.context_window, defaults.context_window)
        self.assertEqual(config.max_steps, defaults.max_steps)

    def test_numeric_strings_are_coerced(self):
        self.write(
            {
                "context_window": "64000",
                "max_steps": "7",
                "timeout": "12.5",
                "stream_usage": "false",
                "verbose": "no",
            }
        )
        config = load_config()
        self.assertEqual(config.context_window, 64000)
        self.assertEqual(config.max_steps, 7)
        self.assertEqual(config.timeout, 12.5)
        self.assertFalse(config.stream_usage)
        self.assertFalse(config.verbose)

    def test_unknown_keys_and_bad_containers_are_ignored(self):
        self.write({"unknown": 1, "model": {"nested": True}})
        with mock.patch("sys.stderr"):
            config = load_config()
        self.assertEqual(config.model, Config().model)

    def test_valid_values_are_kept(self):
        self.write({"model": "deepseek-chat", "permission": "auto", "max_steps": 0})
        config = load_config()
        self.assertEqual(config.model, "deepseek-chat")
        self.assertEqual(config.permission, "auto")
        self.assertEqual(config.max_steps, 0)


if __name__ == "__main__":
    unittest.main()
