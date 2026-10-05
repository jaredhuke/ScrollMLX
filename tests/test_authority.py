"""Unit tests for the AGMO authority model. Run with:

    PYTHONPATH=. python tests/test_authority.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import authority as au  # noqa: E402


class AuthorityTest(unittest.TestCase):
    def test_read_only_level(self):
        self.assertTrue(au.allowed("A2", "read_file"))
        self.assertTrue(au.allowed("A2", "grep_codebase"))
        self.assertFalse(au.allowed("A2", "write_file"))
        self.assertFalse(au.allowed("A2", "edit_file"))
        self.assertFalse(au.allowed("A2", "run_command"))

    def test_write_level(self):
        self.assertTrue(au.allowed("A3", "write_file"))
        self.assertTrue(au.allowed("A3", "append_file"))
        self.assertFalse(au.allowed("A3", "run_command"))

    def test_shell_level(self):
        self.assertTrue(au.allowed("A4", "run_command"))
        self.assertTrue(au.allowed("A4", "read_file"))

    def test_advisory_has_no_tools(self):
        self.assertFalse(au.allowed("A1", "read_file"))
        self.assertFalse(au.allowed("A1", "run_command"))

    def test_deny_by_default_for_unknown_tool(self):
        self.assertFalse(au.allowed("A5", "some_future_tool"))

    def test_defaults(self):
        self.assertEqual(au.default_for("mlx"), "A4")
        self.assertEqual(au.default_for("anthropic"), "A2")
        self.assertEqual(au.default_for("openai"), "A2")
        self.assertEqual(au.default_for("some-plugin"), "A2")
        self.assertEqual(au.default_for(None), "A2")

    def test_normalize(self):
        self.assertEqual(au.normalize("a3"), "A3")
        self.assertEqual(au.normalize(None), "A2")
        self.assertEqual(au.normalize("bogus"), "A2")


if __name__ == "__main__":
    unittest.main(verbosity=2)
