#!/usr/bin/env python3
"""Validate hook projection shape used by the native adapter harness."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = ROOT.parent
CLI = REPOSITORY_ROOT / "CLI"
sys.path.insert(0, str(ROOT / "conformance"))

import run_adapter  # noqa: E402


def load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


class HookProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._workspace = tempfile.TemporaryDirectory()
        cls.agents_bin = Path(cls._workspace.name) / "agents"
        env = {
            **os.environ,
            "GOCACHE": os.environ.get("GOCACHE", "/tmp/agents-gocache"),
            "GOPATH": os.environ.get("GOPATH", "/tmp/agents-gopath"),
        }
        subprocess.run(
            ["go", "build", "-buildvcs=false", "-o", str(cls.agents_bin), "./cmd/agents"],
            cwd=CLI,
            env=env,
            check=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._workspace.cleanup()

    def repository(self) -> Path:
        directory = Path(self._workspace.name) / self.id().split(".")[-1]
        directory.mkdir()
        run_adapter.fixture(directory)
        return directory

    def apply_vendor(self, vendor: str) -> Path:
        repository = self.repository()
        subprocess.run(
            [str(self.agents_bin), "apply", "--vendor", vendor, "--root", str(repository)],
            check=True,
        )
        return repository

    def test_disabled_case_requires_activity_without_hook_markers(self) -> None:
        # This executable simulates native output only. These are runner tests,
        # not native conformance evidence.
        for vendor in ("copilot", "codex", "claude"):
            for marker in (None, "native-hook", "native-session-hook"):
                with self.subTest(vendor=vendor, marker=marker):
                    workspace = Path(tempfile.mkdtemp(dir=self._workspace.name))
                    executable = workspace / "fake-harness"
                    executable.write_text(
                        "#!/usr/bin/env python3\n"
                        "from pathlib import Path\nimport json\n"
                        "p=Path('.agents/conformance/markers.jsonl')\n"
                        f"p.write_text(''.join(json.dumps({{'marker': m}})+'\\n' for m in {['root-instruction'] + ([marker] if marker else [])!r}))\n"
                    )
                    executable.chmod(0o755)
                    with mock.patch.object(run_adapter, "native_mcp_markers", return_value=["root-instruction"]):
                        result = run_adapter.disabled_hook_case(vendor, str(self.agents_bin),
                                                                str(executable), workspace / "repository", dict(os.environ))
                    self.assertEqual(result["passed"], marker is None)
                    self.assertEqual(result["mode"], "catalogue-disabled" if vendor == "copilot" else "profile-removal")

    def test_shared_hook_fixtures_agree_with_cli_and_schema(self) -> None:
        from jsonschema import Draft202012Validator
        spec = REPOSITORY_ROOT / "SPEC"
        schema = json.loads((spec / "spec/1.0/schemas/hooks.schema.json").read_text())
        validator = Draft202012Validator(schema)
        repository = self.repository()
        paths = list((spec / "examples/invalid").glob("hooks-*.json"))
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path=path.name):
                document = json.loads(path.read_text())
                self.assertFalse(validator.is_valid(document))
                (repository / ".agents/hooks/hooks.json").write_text(json.dumps(document))
                result = subprocess.run([str(self.agents_bin), "validate", "--root", str(repository)],
                                        capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
        for document in ({"hooks": {}}, {"hooks": {}, "disableAllHooks": True},
                         {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo test", "timeoutSec": 0}]}]}}):
            self.assertTrue(validator.is_valid(document))
            (repository / ".agents/hooks/hooks.json").write_text(json.dumps(document))
            result = subprocess.run([str(self.agents_bin), "validate", "--root", str(repository)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_copilot_hook_projection_uses_native_path_and_lowercase_events(self) -> None:
        repository = self.apply_vendor("copilot")
        document = load_json(repository / ".github/hooks/open-dot-agents.json")

        self.assertEqual(document["version"], 1)
        hooks = document["hooks"]
        self.assertIsInstance(hooks, dict)
        self.assertEqual(set(hooks), {"sessionStart", "userPromptSubmitted", "preToolUse"})
        self.assertEqual(hooks["sessionStart"][0]["type"], "command")
        self.assertIn("hook_marker.py", hooks["sessionStart"][0]["command"])
        self.assertIn("native-session-hook", hooks["sessionStart"][0]["command"])
        self.assertIn("native-hook", hooks["preToolUse"][0]["command"])

    def test_codex_hook_projection_uses_native_path_and_timeout_field(self) -> None:
        repository = self.apply_vendor("codex")
        document = load_json(repository / ".codex/hooks.json")

        hooks = document["hooks"]
        self.assertIsInstance(hooks, dict)
        self.assertEqual(set(hooks), {"SessionStart", "UserPromptSubmit", "PreToolUse"})
        self.assertEqual(hooks["SessionStart"][0]["hooks"][0]["type"], "command")
        self.assertIn("hook_marker.py", hooks["SessionStart"][0]["hooks"][0]["command"])
        self.assertIn("native-session-hook", hooks["SessionStart"][0]["hooks"][0]["command"])
        self.assertIn("native-hook", hooks["PreToolUse"][0]["hooks"][0]["command"])
        self.assertNotIn("timeoutSec", hooks["SessionStart"][0]["hooks"][0])

    def test_claude_hook_projection_merges_into_settings(self) -> None:
        repository = self.repository()
        settings = repository / ".claude/settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text('{"theme":"dark"}\n', encoding="utf-8")
        subprocess.run(
            [str(self.agents_bin), "apply", "--vendor", "claude", "--root", str(repository)],
            check=True,
        )
        document = load_json(settings)

        self.assertEqual(document["theme"], "dark")
        hooks = document["hooks"]
        self.assertIsInstance(hooks, dict)
        self.assertEqual(set(hooks), {"SessionStart", "UserPromptSubmit", "PreToolUse"})
        self.assertEqual(hooks["SessionStart"][0]["hooks"][0]["type"], "command")
        self.assertIn("hook_marker.py", hooks["SessionStart"][0]["hooks"][0]["command"])
        self.assertIn("native-session-hook", hooks["SessionStart"][0]["hooks"][0]["command"])
        self.assertIn("native-hook", hooks["PreToolUse"][0]["hooks"][0]["command"])

    def test_hook_marker_is_idempotent(self) -> None:
        repository = self.repository()
        marker = repository / ".agents/conformance/hook_marker.py"
        log = repository / ".agents/conformance/markers.jsonl"

        subprocess.run(["python3", str(marker)], check=True)
        subprocess.run(["python3", str(marker)], check=True)
        subprocess.run(["python3", str(marker), "native-session-hook"], check=True)
        subprocess.run(["python3", str(marker), "native-session-hook"], check=True)

        markers = [
            json.loads(line)["marker"]
            for line in log.read_text(encoding="utf-8").splitlines()
            if line
        ]
        self.assertEqual(markers.count("native-hook"), 1)
        self.assertEqual(markers.count("native-session-hook"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
