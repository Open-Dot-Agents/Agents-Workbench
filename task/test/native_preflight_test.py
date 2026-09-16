#!/usr/bin/env python3
"""Validate native harness binary resolution."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]

import sys

sys.path.insert(0, str(ROOT / "conformance"))

import run_adapter  # noqa: E402


def fake_executable(directory: Path, name: str, output: str) -> Path:
    path = directory / name
    path.write_text(f"#!/bin/sh\nprintf '%s\\n' {output!r}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


class NativePreflightTests(unittest.TestCase):
    def test_preflight_uses_explicit_vendor_binary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = fake_executable(Path(temporary), "codex-dev", "codex-cli 0.154.0")
            with mock.patch.dict(
                os.environ,
                {
                    "AGENTS_BIN": "/tmp/agents",
                    "CODEX_BIN": str(executable),
                    "OPENAI_API_KEY": "test-key",
                },
                clear=False,
            ):
                checks, metadata = run_adapter.preflight("codex")

        checks_by_id = {str(check["id"]): check["passed"] for check in checks}
        self.assertTrue(checks_by_id["preflight.agents.bin"])
        self.assertTrue(checks_by_id["preflight.codex.installed"])
        self.assertTrue(checks_by_id["preflight.codex.version"])
        self.assertTrue(checks_by_id["preflight.openai.api.key"])
        self.assertTrue(checks_by_id["preflight.codex.credential"])
        self.assertEqual(metadata["harnessBinaryEnv"], "CODEX_BIN")
        self.assertEqual(metadata["harnessPath"], str(executable))
        self.assertEqual(metadata["harnessVersionOutput"], "codex-cli 0.154.0")
        self.assertEqual(metadata["credentialEnv"], "OPENAI_API_KEY")
        self.assertEqual(metadata["acceptedCredentialEnv"], "OPENAI_API_KEY,CODEX_ACCESS_TOKEN")

    def test_preflight_accepts_codex_access_token(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = fake_executable(Path(temporary), "codex", "codex-cli 0.154.0")
            with mock.patch.dict(
                os.environ,
                {
                    "PATH": temporary,
                    "AGENTS_BIN": "/tmp/agents",
                    "CODEX_ACCESS_TOKEN": "test-token",
                },
                clear=True,
            ):
                checks, metadata = run_adapter.preflight("codex")

        checks_by_id = {str(check["id"]): check["passed"] for check in checks}
        self.assertTrue(checks_by_id["preflight.openai.api.key"])
        self.assertTrue(checks_by_id["preflight.codex.credential"])
        self.assertEqual(metadata["credentialEnv"], "CODEX_ACCESS_TOKEN")

    def test_codex_access_token_is_imported_into_isolated_home(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            executable = directory / "codex"
            executable.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = \"login\" ]; then mkdir -p \"$CODEX_HOME\"; cat > \"$CODEX_HOME/token.txt\"; exit 0; fi\n"
                "printf '%s\\n' 'codex-cli 0.154.0'\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            metadata: dict[str, object] = {"credentialEnv": "CODEX_ACCESS_TOKEN"}
            with mock.patch.dict(os.environ, {"CODEX_ACCESS_TOKEN": "secret-token"}, clear=False):
                environment = run_adapter.harness_environment("codex", str(executable), metadata, directory)

            self.assertEqual((Path(environment["CODEX_HOME"]) / "token.txt").read_text(encoding="utf-8"), "secret-token")
            self.assertEqual(metadata["credentialImport"], "codex.login.with-access-token")

    def test_redact_masks_all_accepted_credentials(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "COPILOT_GITHUB_TOKEN": "copilot-secret",
                "CODEX_ACCESS_TOKEN": "codex-secret",
                "CLAUDE_CODE_OAUTH_TOKEN": "claude-secret",
            },
            clear=True,
        ):
            redacted = run_adapter.redact("copilot-secret codex-secret claude-secret")

        self.assertEqual(redacted, "[REDACTED] [REDACTED] [REDACTED]")

    def test_preflight_accepts_copilot_documented_token_alternatives(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = fake_executable(Path(temporary), "copilot", "GitHub Copilot CLI 1.0.84-9.")
            with mock.patch.dict(
                os.environ,
                {
                    "PATH": temporary,
                    "AGENTS_BIN": "/tmp/agents",
                    "GITHUB_TOKEN": "test-token",
                },
                clear=True,
            ):
                checks, metadata = run_adapter.preflight("copilot")

        checks_by_id = {str(check["id"]): check["passed"] for check in checks}
        self.assertTrue(checks_by_id["preflight.gh.token"])
        self.assertTrue(checks_by_id["preflight.copilot.credential"])
        self.assertEqual(metadata["credentialEnv"], "GITHUB_TOKEN")
        self.assertEqual(metadata["acceptedCredentialEnv"], "COPILOT_GITHUB_TOKEN,GH_TOKEN,GITHUB_TOKEN")

    def test_preflight_accepts_claude_oauth_token(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = fake_executable(Path(temporary), "claude", "2.1.229 (Claude Code)")
            with mock.patch.dict(
                os.environ,
                {
                    "PATH": temporary,
                    "AGENTS_BIN": "/tmp/agents",
                    "CLAUDE_CODE_OAUTH_TOKEN": "test-token",
                },
                clear=True,
            ):
                checks, metadata = run_adapter.preflight("claude")

        checks_by_id = {str(check["id"]): check["passed"] for check in checks}
        self.assertTrue(checks_by_id["preflight.anthropic.api.key"])
        self.assertTrue(checks_by_id["preflight.claude.credential"])
        self.assertEqual(metadata["credentialEnv"], "CLAUDE_CODE_OAUTH_TOKEN")
        self.assertEqual(metadata["acceptedCredentialEnv"], "ANTHROPIC_API_KEY,ANTHROPIC_AUTH_TOKEN,CLAUDE_CODE_OAUTH_TOKEN")

    def test_commands_use_preflighted_executable(self) -> None:
        executable = "/opt/native-harness/bin/claude"
        command = run_adapter.command("claude", executable, Path("/tmp/project"), "prompt")

        self.assertEqual(command[0], executable)
        self.assertIn("--dangerously-skip-permissions", command)

    def test_version_match_rejects_prefix_and_prerelease(self) -> None:
        for version in ("0.154.01", "10.154.0", "0.154.0-rc1", "0.154.0+local"):
            self.assertFalse(run_adapter.matches_pinned_version("codex-cli " + version, "0.154.0"))
        self.assertTrue(run_adapter.matches_pinned_version("codex-cli 0.154.0", "0.154.0"))

    def test_version_match_accepts_copilot_sentence_punctuation(self) -> None:
        self.assertTrue(run_adapter.matches_pinned_version(
            "GitHub Copilot CLI 1.0.84-9.\nRun 'copilot update' to check for updates.", "1.0.84-9"))
        self.assertFalse(run_adapter.matches_pinned_version("GitHub Copilot CLI 1.0.84-9.1", "1.0.84-9"))

    def test_version_command_uses_preflighted_executable(self) -> None:
        self.assertEqual(
            run_adapter.version_command("/opt/native-harness/bin/copilot"),
            ["/opt/native-harness/bin/copilot", "--version"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
