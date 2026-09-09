#!/usr/bin/env python3
"""Validate native evidence summary output."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SUMMARY = ROOT / "conformance/summarize_results.py"


def write_result(directory: Path, vendor: str, passed: bool, failed_check: str | None = None) -> None:
    checks: list[dict[str, object]] = []
    if failed_check:
        checks.append({"id": failed_check, "passed": False})
    result = {
        "schemaVersion": "1.0.0",
        "standardVersion": "1.0.0",
        "implementation": f"reference-cli-{vendor}",
        "implementationVersion": "dev",
        "class": "adapter",
        "passed": passed,
        "checks": checks,
        "metadata": {"harness": vendor, "runMode": "native"},
    }
    (directory / f"{vendor}.json").write_text(json.dumps(result) + "\n", encoding="utf-8")


class NativeSummaryTests(unittest.TestCase):
    def test_summary_reports_failed_checks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result_dir = Path(temporary)
            write_result(result_dir, "copilot", False, "preflight.gh.token")
            write_result(result_dir, "codex", True)
            write_result(result_dir, "claude", False, "native.hook")
            completed = subprocess.run(
                ["python3", str(SUMMARY), "--result-dir", str(result_dir)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertEqual(completed.returncode, 1)
        self.assertIn("copilot: failed (preflight.gh.token)", completed.stdout)
        self.assertIn("codex: passed", completed.stdout)
        self.assertIn("claude: failed (native.hook)", completed.stdout)

    def test_summary_collapses_legacy_credential_checks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result_dir = Path(temporary)
            result = {
                "schemaVersion": "1.0.0",
                "standardVersion": "1.0.0",
                "implementation": "reference-cli-copilot",
                "implementationVersion": "dev",
                "class": "adapter",
                "passed": False,
                "checks": [
                    {"id": "preflight.gh.token", "passed": False},
                    {"id": "preflight.copilot.credential", "passed": False},
                ],
                "metadata": {"harness": "copilot", "runMode": "native"},
            }
            (result_dir / "copilot.json").write_text(json.dumps(result) + "\n", encoding="utf-8")
            write_result(result_dir, "codex", True)
            write_result(result_dir, "claude", True)
            completed = subprocess.run(
                ["python3", str(SUMMARY), "--result-dir", str(result_dir)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertEqual(completed.returncode, 1)
        self.assertIn("copilot: failed (preflight.copilot.credential)", completed.stdout)
        self.assertNotIn("preflight.gh.token", completed.stdout)

    def test_priority_group_does_not_require_claude(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            write_result(directory, "codex", True)
            write_result(directory, "copilot", True)
            completed = subprocess.run(["python3", str(SUMMARY), "--result-dir", temporary,
                                        "--vendors", "codex", "copilot"], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("claude", completed.stdout)

    def test_summary_reports_missing_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            completed = subprocess.run(
                ["python3", str(SUMMARY), "--result-dir", temporary, "--suffix=-preflight"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertEqual(completed.returncode, 1)
        self.assertIn("copilot: missing", completed.stdout)
        self.assertIn("codex: missing", completed.stdout)
        self.assertIn("claude: missing", completed.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
