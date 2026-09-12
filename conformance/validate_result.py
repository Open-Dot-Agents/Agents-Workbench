#!/usr/bin/env python3
"""Validate one evidence result against the published result contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from run_adapter import matches_pinned_version


VERSIONS = json.loads((Path(__file__).parent / "versions.json").read_text(encoding="utf-8"))
REQUIRED_ADAPTER_CHECKS = {
    "native.root.instruction",
    "native.nested.instruction",
    "native.portable.skill",
    "native.mcp.stdio",
    "native.hook",
    "native.hook.session",
    "native.hook.disabled",
}
REQUIRED_MARKER_CHECKS = {
    "root-instruction": "native.root.instruction",
    "nested-instruction": "native.nested.instruction",
    "portable-skill": "native.portable.skill",
    "native-hook": "native.hook",
    "native-session-hook": "native.hook.session",
}
REQUIRED_NATIVE_CASES = {
    "root-instruction",
    "nested-instruction",
    "portable-skill",
}
REQUIRED_CREDENTIAL_CHECKS = {
    "copilot": "preflight.copilot.credential",
    "codex": "preflight.codex.credential",
    "claude": "preflight.claude.credential",
}
ACCEPTED_CREDENTIALS = {
    "copilot": {"COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"},
    "codex": {"OPENAI_API_KEY", "CODEX_ACCESS_TOKEN"},
    "claude": {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"},
}


def required_preflight_checks(harness: str, auth_mode: str) -> set[str]:
    if harness not in REQUIRED_CREDENTIAL_CHECKS:
        raise ValueError("preflight must identify a stable harness")
    if auth_mode not in {"environment", "existing-login"}:
        raise ValueError("invalid authMode")
    if auth_mode == "existing-login" and harness not in {"codex", "copilot"}:
        raise ValueError("existing-login is unavailable for this harness")
    required = {
        "preflight.agents.bin",
        f"preflight.{harness}.installed",
        f"preflight.{harness}.version",
        f"preflight.{harness}.existing-login" if auth_mode == "existing-login" else REQUIRED_CREDENTIAL_CHECKS[harness],
    }
    if auth_mode == "existing-login" and harness == "codex":
        required.add("preflight.codex.login-status")
    return required


def validate_adapter_semantics(result: dict[str, object]) -> None:
    if result.get("class") != "adapter" or not result.get("passed"):
        return
    metadata = result.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("passed adapter result metadata must be an object")
    harness = metadata.get("harness")
    if harness not in REQUIRED_CREDENTIAL_CHECKS:
        raise ValueError("passed adapter result metadata must identify a stable harness")
    run_mode = metadata.get("runMode")
    if run_mode not in {"preflight", "native"}:
        raise ValueError("passed adapter result metadata must define runMode")
    for field in ("harnessPath", "agentsBin"):
        if not isinstance(metadata.get(field), str) or not metadata[field]:
            raise ValueError(f"passed adapter result metadata must define {field}")
    if not isinstance(metadata.get("harnessVersionOutput"), str) or not metadata["harnessVersionOutput"]:
        raise ValueError("passed adapter result metadata must define harnessVersionOutput")
    if run_mode == "native" and (not isinstance(metadata.get("harnessVersion"), str) or not metadata["harnessVersion"]):
        raise ValueError("passed native adapter result metadata must define harnessVersion")
    accepted_credential_env = metadata.get("acceptedCredentialEnv")
    if not isinstance(accepted_credential_env, str) or not accepted_credential_env:
        raise ValueError("passed adapter result metadata must define acceptedCredentialEnv")
    auth_mode = metadata.get("authMode", "environment")
    if auth_mode == "existing-login":
        if harness not in {"codex", "copilot"} or metadata.get("authenticationVerification") != "native-request-required":
            raise ValueError("invalid existing-login authentication metadata")
    elif auth_mode == "environment":
        credential_env = metadata.get("credentialEnv")
        if not isinstance(credential_env, str) or not credential_env:
            raise ValueError("passed adapter result metadata must define credentialEnv")
        accepted_credentials = set(accepted_credential_env.split(","))
        if accepted_credentials != ACCEPTED_CREDENTIALS[harness]:
            raise ValueError("passed adapter result acceptedCredentialEnv must match the harness")
        if credential_env not in ACCEPTED_CREDENTIALS[harness]:
            raise ValueError("passed adapter result credentialEnv must be accepted for the harness")
    else:
        raise ValueError("invalid authMode")
    package = metadata.get("package")
    if not isinstance(package, dict):
        raise ValueError("passed adapter result metadata must define package")
    for field in ("package", "version", "integrity"):
        if not isinstance(package.get(field), str) or not package[field]:
            raise ValueError(f"passed adapter result metadata must define package.{field}")
    expected_package = VERSIONS["harnesses"][harness]
    if package != expected_package:
        raise ValueError("passed adapter result package metadata must match pinned Workbench version")
    if not matches_pinned_version(metadata["harnessVersionOutput"], expected_package["version"]):
        raise ValueError("passed adapter result harnessVersionOutput must include pinned version")
    if run_mode == "native" and not matches_pinned_version(metadata["harnessVersion"], expected_package["version"]):
        raise ValueError("passed native adapter result harnessVersion must include pinned version")

    checks = result.get("checks")
    if not isinstance(checks, list):
        raise ValueError("adapter result checks must be a list")
    passed_checks = {
        item.get("id")
        for item in checks
        if isinstance(item, dict) and item.get("passed") is True
    }
    required = required_preflight_checks(harness, auth_mode)
    if run_mode == "native":
        transcripts = metadata.get("transcripts")
        if not isinstance(transcripts, list) or not transcripts:
            raise ValueError("passed native adapter result metadata must include transcripts")
        transcript_cases = set()
        for transcript in transcripts:
            if not isinstance(transcript, dict):
                raise ValueError("passed native adapter result transcript must be an object")
            case = transcript.get("case")
            if not isinstance(case, str) or not case:
                raise ValueError("passed native adapter result transcript must define case")
            expected_marker = transcript.get("expectedMarker")
            if not isinstance(expected_marker, str) or not expected_marker:
                raise ValueError("passed native adapter result transcript must define expectedMarker")
            if not isinstance(transcript.get("cwd"), str) or not transcript["cwd"]:
                raise ValueError("passed native adapter result transcript must define cwd")
            if not isinstance(transcript.get("output"), str):
                raise ValueError("passed native adapter result transcript must define output")
            if not isinstance(transcript.get("returncode"), int):
                raise ValueError("passed native adapter result transcript must define integer returncode")
            if transcript["returncode"] != 0:
                raise ValueError("passed native adapter result transcript returncode must be zero")
            if case in REQUIRED_NATIVE_CASES:
                if case not in transcript.get("nativeMcpMarkers", []):
                    raise ValueError("native transcript must prove a native MCP call for its marker")
                transcript_cases.add(case)
                if expected_marker != case:
                    raise ValueError("passed native adapter result transcript marker does not match case")
        missing_cases = sorted(REQUIRED_NATIVE_CASES - transcript_cases)
        if missing_cases:
            raise ValueError("passed native adapter result missing transcript cases: " + ", ".join(missing_cases))
        markers = metadata.get("markers")
        if not isinstance(markers, list) or not markers:
            raise ValueError("passed native adapter result metadata must include markers")
        for marker in markers:
            if not isinstance(marker, str) or not marker:
                raise ValueError("passed native adapter result markers must be strings")
        marker_set = set(markers)
        missing_markers = sorted(set(REQUIRED_MARKER_CHECKS) - marker_set)
        if missing_markers:
            raise ValueError("passed native adapter result missing markers: " + ", ".join(missing_markers))
        missing_marker_checks = sorted(
            check_id
            for marker, check_id in REQUIRED_MARKER_CHECKS.items()
            if marker in marker_set and check_id not in passed_checks
        )
        if missing_marker_checks:
            raise ValueError("passed native adapter result marker checks disagree: " + ", ".join(missing_marker_checks))
        disabled = metadata.get("disabledHooks")
        if not isinstance(disabled, dict) or disabled.get("passed") is not True:
            raise ValueError("passed native adapter result must include disabledHooks evidence")
        expected_mode = "catalogue-disabled" if harness == "copilot" else "profile-removal"
        if disabled.get("mode") != expected_mode or disabled.get("returncode") != 0 or not isinstance(disabled.get("output"), str):
            raise ValueError("disabledHooks must identify a successful deactivation run")
        disabled_markers = disabled.get("markers")
        if (not isinstance(disabled_markers, list) or "root-instruction" not in disabled_markers
                or any(marker in disabled_markers for marker in ("native-hook", "native-session-hook", "native-prompt-hook"))):
            raise ValueError("disabledHooks must prove MCP activity without hook execution")
        if "root-instruction" not in disabled.get("nativeMcpMarkers", []):
            raise ValueError("disabledHooks must prove a native MCP call")
        required |= REQUIRED_ADAPTER_CHECKS
    missing = sorted(required - passed_checks)
    if missing:
        raise ValueError("passed adapter result missing checks: " + ", ".join(missing))


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: validate_result.py RESULT SCHEMA", file=sys.stderr)
        return 2
    result = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    schema = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    try:
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(result)
        validate_adapter_semantics(result)
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
