from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ela.paths import resolve_path


class ResolvePathTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cwd = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_relative_path_uses_cwd(self):
        self.assertEqual(
            resolve_path(self.cwd, "src/a.py"),
            (self.cwd / "src" / "a.py").resolve(),
        )

    def test_absolute_path_is_kept(self):
        target = self.cwd / "a.txt"
        self.assertEqual(resolve_path(self.cwd, str(target)), target.resolve())

    def test_parent_traversal_is_normalized(self):
        self.assertEqual(
            resolve_path(self.cwd, "sub/../a.txt"),
            (self.cwd / "a.txt").resolve(),
        )


if __name__ == "__main__":
    unittest.main()
