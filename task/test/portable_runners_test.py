#!/usr/bin/env python3
"""Keep reproducible runners independent of one developer workstation."""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "WORKBENCH/conformance"))

from run_native_approvals import native_binary  # noqa: E402


class PortableRunnersTest(unittest.TestCase):
    def test_runners_do_not_use_workstation_paths(self):
        offenders = []
        for directory in (ROOT / "CLI/scripts", ROOT / "WORKBENCH/conformance"):
            for path in directory.glob("*.py"):
                source = path.read_text(encoding="utf-8")
                if "/mnt/DATA/tmp" in source or "/home/maurizio" in source:
                    offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_explicit_native_binary_must_be_absolute(self):
        with mock.patch.dict(os.environ, {"CODEX_BIN": "relative/codex"}, clear=False):
            with self.assertRaisesRegex(ValueError, "must be absolute"):
                native_binary("codex")

        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "codex"
            binary.write_text("fixture", encoding="utf-8")
            with mock.patch.dict(os.environ, {"CODEX_BIN": str(binary)}, clear=False):
                self.assertEqual(native_binary("codex"), binary)


if __name__ == "__main__":
    unittest.main()
