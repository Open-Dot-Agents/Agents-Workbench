#!/usr/bin/env python3
"""Validate Workbench adapter evidence checks."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT.parent / "SPEC/spec/1.0/schemas/conformance-result.schema.json"
VALIDATOR = ROOT / "conformance/validate_result.py"
VERSIONS = json.loads((ROOT / "conformance/versions.json").read_text(encoding="utf-8"))


def required_checks() -> list[dict[str, object]]:
    return [
        {"id": "preflight.agents.bin", "passed": True},
        {"id": "preflight.codex.installed", "passed": True},
        {"id": "preflight.codex.version", "passed": True},
        {"id": "preflight.openai.api.key", "passed": True},
        {"id": "preflight.codex.credential", "passed": True},
        {"id": "native.root.instruction", "passed": True},
        {"id": "native.nested.instruction", "passed": True},
        {"id": "native.portable.skill", "passed": True},
        {"id": "native.mcp.stdio", "passed": True},
        {"id": "native.hook", "passed": True},
        {"id": "native.hook.session", "passed": True},
        {"id": "native.hook.disabled", "passed": True},
    ]


def required_metadata(run_mode: str = "native") -> dict[str, object]:
    return {
        "harness": "codex",
        "runMode": run_mode,
        "package": VERSIONS["harnesses"]["codex"],
        "platform": "Linux x86_64",
        "testedAt": "2026-08-13T00:00:00+00:00",
        "agentsBin": "/tmp/agents",
        "harnessPath": "/usr/bin/codex",
        "harnessVersionOutput": "codex-cli 0.153.4",
        "acceptedCredentialEnv": "OPENAI_API_KEY,CODEX_ACCESS_TOKEN",
        "credentialEnv": "OPENAI_API_KEY",
        "markers": ["root-instruction", "nested-instruction", "portable-skill", "native-hook", "native-session-hook"],
        "disabledHooks": {"passed": True, "mode": "profile-removal", "returncode": 0,
                          "output": "{}", "nativeMcpMarkers": ["root-instruction"], "markers": ["root-instruction"]},
        "transcripts": [
            {
                "case": "root-instruction",
                "expectedMarker": "root-instruction",
                "nativeMcpMarkers": ["root-instruction"],
                "cwd": ".",
                "returncode": 0,
                "output": "{}",
            },
            {
                "case": "nested-instruction",
                "expectedMarker": "nested-instruction",
                "nativeMcpMarkers": ["nested-instruction"],
                "cwd": "packages/api",
                "returncode": 0,
                "output": "{}",
            },
            {
                "case": "portable-skill",
                "expectedMarker": "portable-skill",
                "nativeMcpMarkers": ["portable-skill"],
                "cwd": ".",
                "returncode": 0,
                "output": "{}",
            },
        ],
        **({"harnessVersion": "codex-cli 0.153.4"} if run_mode == "native" else {}),
    }


def write_result(
    directory: Path,
    checks: list[dict[str, object]],
    metadata: dict[str, object] | None = None,
) -> Path:
    result = {
        "schemaVersion": "1.0.0",
        "standardVersion": "1.0.0",
        "implementation": "reference-cli-codex",
        "implementationVersion": "dev",
        "class": "adapter",
        "passed": True,
        "checks": checks,
        "metadata": metadata if metadata is not None else required_metadata(),
    }
    path = directory / "result.json"
    path.write_text(json.dumps(result) + "\n", encoding="utf-8")
    return path


class EvidenceValidationTests(unittest.TestCase):
    def test_passed_adapter_result_requires_native_hook_check(self) -> None:
        checks = [check for check in required_checks() if check["id"] != "native.hook"]
        with tempfile.TemporaryDirectory() as temporary:
            result = write_result(Path(temporary), checks)
            completed = subprocess.run(
                ["python3", str(VALIDATOR), str(result), str(SCHEMA)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("native.hook", completed.stderr)

    def test_passed_adapter_result_requires_vendor_credential_check(self) -> None:
        checks = [check for check in required_checks() if check["id"] != "preflight.codex.credential"]
        with tempfile.TemporaryDirectory() as temporary:
            result = write_result(Path(temporary), checks)
            completed = subprocess.run(
                ["python3", str(VALIDATOR), str(result), str(SCHEMA)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("preflight.codex.credential", completed.stderr)

    def test_passed_adapter_result_requires_runtime_metadata(self) -> None:
        metadata = required_metadata()
        metadata.pop("transcripts")
        with tempfile.TemporaryDirectory() as temporary:
            result = write_result(Path(temporary), required_checks(), metadata)
            completed = subprocess.run(
                ["python3", str(VALIDATOR), str(result), str(SCHEMA)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("transcripts", completed.stderr)

    def test_passed_adapter_result_requires_package_provenance(self) -> None:
        metadata = required_metadata()
        package = dict(metadata["package"])
        package.pop("integrity")
        metadata["package"] = package
        with tempfile.TemporaryDirectory() as temporary:
            result = write_result(Path(temporary), required_checks(), metadata)
            completed = subprocess.run(
                ["python3", str(VALIDATOR), str(result), str(SCHEMA)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("package.integrity", completed.stderr)

    def test_passed_adapter_result_requires_credential_metadata(self) -> None:
        metadata = required_metadata()
        metadata.pop("credentialEnv")
        with tempfile.TemporaryDirectory() as temporary:
            result = write_result(Path(temporary), required_checks(), metadata)
            completed = subprocess.run(
                ["python3", str(VALIDATOR), str(result), str(SCHEMA)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("credentialEnv", completed.stderr)

    def test_passed_adapter_result_rejects_unaccepted_credential_metadata(self) -> None:
        metadata = required_metadata()
        metadata["credentialEnv"] = "OTHER_TOKEN"
        with tempfile.TemporaryDirectory() as temporary:
            result = write_result(Path(temporary), required_checks(), metadata)
            completed = subprocess.run(
                ["python3", str(VALIDATOR), str(result), str(SCHEMA)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("credentialEnv must be accepted", completed.stderr)

    def test_passed_adapter_result_rejects_unpinned_package(self) -> None:
        metadata = required_metadata()
        package = dict(metadata["package"])
        package["integrity"] = "sha512-other"
        metadata["package"] = package
        with tempfile.TemporaryDirectory() as temporary:
            result = write_result(Path(temporary), required_checks(), metadata)
            completed = subprocess.run(
                ["python3", str(VALIDATOR), str(result), str(SCHEMA)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("pinned Workbench version", completed.stderr)

    def test_passed_adapter_result_requires_version_output(self) -> None:
        metadata = required_metadata()
        metadata.pop("harnessVersionOutput")
        with tempfile.TemporaryDirectory() as temporary:
            result = write_result(Path(temporary), required_checks(), metadata)
            completed = subprocess.run(
                ["python3", str(VALIDATOR), str(result), str(SCHEMA)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("harnessVersionOutput", completed.stderr)

    def test_passed_adapter_result_rejects_mismatched_version_output(self) -> None:
        metadata = required_metadata()
        metadata["harnessVersionOutput"] = "codex-cli 0.0.0"
        with tempfile.TemporaryDirectory() as temporary:
            result = write_result(Path(temporary), required_checks(), metadata)
            completed = subprocess.run(
                ["python3", str(VALIDATOR), str(result), str(SCHEMA)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("pinned version", completed.stderr)

    def test_passed_preflight_result_accepts_preflight_checks_only(self) -> None:
        checks = [
            {"id": "preflight.agents.bin", "passed": True},
            {"id": "preflight.codex.installed", "passed": True},
            {"id": "preflight.codex.version", "passed": True},
            {"id": "preflight.openai.api.key", "passed": True},
            {"id": "preflight.codex.credential", "passed": True},
        ]
        with tempfile.TemporaryDirectory() as temporary:
            result = write_result(Path(temporary), checks, required_metadata("preflight"))
            completed = subprocess.run(
                ["python3", str(VALIDATOR), str(result), str(SCHEMA)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_passed_native_result_requires_run_mode(self) -> None:
        metadata = required_metadata()
        metadata.pop("runMode")
        with tempfile.TemporaryDirectory() as temporary:
            result = write_result(Path(temporary), required_checks(), metadata)
            completed = subprocess.run(
                ["python3", str(VALIDATOR), str(result), str(SCHEMA)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("runMode", completed.stderr)

    def test_passed_native_result_rejects_missing_hook_marker(self) -> None:
        metadata = required_metadata()
        metadata["markers"] = ["root-instruction", "nested-instruction", "portable-skill"]
        with tempfile.TemporaryDirectory() as temporary:
            result = write_result(Path(temporary), required_checks(), metadata)
            completed = subprocess.run(
                ["python3", str(VALIDATOR), str(result), str(SCHEMA)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("native-hook", completed.stderr)

    def test_passed_native_result_rejects_nonzero_transcript_returncode(self) -> None:
        metadata = required_metadata()
        metadata["transcripts"][0]["returncode"] = 1
        with tempfile.TemporaryDirectory() as temporary:
            result = write_result(Path(temporary), required_checks(), metadata)
            completed = subprocess.run(
                ["python3", str(VALIDATOR), str(result), str(SCHEMA)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("returncode", completed.stderr)

    def test_passed_native_result_rejects_malformed_transcript(self) -> None:
        metadata = required_metadata()
        metadata["transcripts"][0]["returncode"] = "0"
        with tempfile.TemporaryDirectory() as temporary:
            result = write_result(Path(temporary), required_checks(), metadata)
            completed = subprocess.run(
                ["python3", str(VALIDATOR), str(result), str(SCHEMA)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("integer returncode", completed.stderr)

    def test_passed_native_result_requires_all_transcript_cases(self) -> None:
        metadata = required_metadata()
        metadata["transcripts"] = metadata["transcripts"][:2]
        with tempfile.TemporaryDirectory() as temporary:
            result = write_result(Path(temporary), required_checks(), metadata)
            completed = subprocess.run(
                ["python3", str(VALIDATOR), str(result), str(SCHEMA)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("portable-skill", completed.stderr)

    def test_passed_native_result_rejects_mismatched_transcript_marker(self) -> None:
        metadata = required_metadata()
        metadata["transcripts"][0]["expectedMarker"] = "nested-instruction"
        with tempfile.TemporaryDirectory() as temporary:
            result = write_result(Path(temporary), required_checks(), metadata)
            completed = subprocess.run(
                ["python3", str(VALIDATOR), str(result), str(SCHEMA)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("marker does not match case", completed.stderr)

    def test_disabled_evidence_rejects_hook_execution_or_missing_activity(self) -> None:
        for markers in ([], ["root-instruction", "native-hook"], ["root-instruction", "native-session-hook"]):
            metadata = required_metadata()
            metadata["disabledHooks"]["markers"] = markers
            with tempfile.TemporaryDirectory() as temporary:
                result = write_result(Path(temporary), required_checks(), metadata)
                completed = subprocess.run(["python3", str(VALIDATOR), str(result), str(SCHEMA)],
                                           capture_output=True, text=True)
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("disabledHooks", completed.stderr)

    def test_existing_login_evidence_does_not_require_environment_tokens(self) -> None:
        metadata = required_metadata()
        metadata.pop("credentialEnv")
        metadata.update(authMode="existing-login", authenticationVerification="native-request-required")
        checks = [c for c in required_checks() if c["id"] not in {"preflight.openai.api.key", "preflight.codex.credential"}]
        checks += [{"id":"preflight.codex.existing-login","passed":True}, {"id":"preflight.codex.login-status","passed":True}]
        with tempfile.TemporaryDirectory() as temporary:
            result = write_result(Path(temporary), checks, metadata)
            completed = subprocess.run(["python3", str(VALIDATOR), str(result), str(SCHEMA)], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_native_evidence_rejects_marker_without_native_tool_call(self) -> None:
        metadata = required_metadata()
        metadata["transcripts"][0]["nativeMcpMarkers"] = []
        with tempfile.TemporaryDirectory() as temporary:
            result = write_result(Path(temporary), required_checks(), metadata)
            completed = subprocess.run(["python3", str(VALIDATOR), str(result), str(SCHEMA)], capture_output=True, text=True)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("native MCP call", completed.stderr)

    def test_passed_adapter_result_accepts_required_native_checks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = write_result(Path(temporary), required_checks())
            completed = subprocess.run(
                ["python3", str(VALIDATOR), str(result), str(SCHEMA)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
