#!/usr/bin/env python3
"""Run version-pinned native harness acceptance and emit durable JSON evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path


RUNNER_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
ROOT = Path(__file__).resolve().parents[2]
WORKBENCH = Path(__file__).resolve().parents[1]
VERSIONS = json.loads((Path(__file__).parent / "versions.json").read_text())
EXPECTED_MARKERS = ["root-instruction", "nested-instruction", "portable-skill", "native-hook", "native-session-hook"]
MARKER_CHECKS = {
    "root-instruction": "native.root.instruction",
    "nested-instruction": "native.nested.instruction",
    "portable-skill": "native.portable.skill",
    "native-hook": "native.hook",
    "native-session-hook": "native.hook.session",
}
ACCEPTED_CREDENTIALS = {
    "copilot": ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"),
    "codex": ("OPENAI_API_KEY", "CODEX_ACCESS_TOKEN"),
    "claude": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"),
}
HARNESS_BIN_ENV = {
    "copilot": "COPILOT_BIN",
    "codex": "CODEX_BIN",
    "claude": "CLAUDE_BIN",
}


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def fixture(directory: Path) -> None:
    write(directory / ".agents/AGENTS.md", """# Root conformance instructions

When the prompt contains `ODA_ROOT_CONFORMANCE`, call the `oda-marker` tool
named `record` exactly once with marker `root-instruction`.
Use the native MCP tool only. Do not run marker scripts or write marker files.
If the tool is unavailable, report that failure without a workaround.
""")
    (directory / "AGENTS.md").symlink_to(".agents/AGENTS.md")
    write(directory / "packages/api/AGENTS.md", """# Nested conformance instructions

When the prompt contains `ODA_NESTED_CONFORMANCE`, call the `oda-marker` tool
named `record` exactly once with marker `nested-instruction`.
""")
    write(directory / ".agents/manifest.json", json.dumps({
        "version": "1.0.0", "profiles": ["tools", "hooks", "skills"]
    }, indent=2) + "\n")
    marker_server = directory / ".agents/conformance/marker_server.py"
    marker_log = directory / ".agents/conformance/markers.jsonl"
    marker_server.parent.mkdir(parents=True, exist_ok=True)
    write(directory / ".agents/tools/mcp.json", json.dumps({"mcpServers": {
        "oda-marker": {
            "type": "stdio", "command": "python3",
            "args": [str(marker_server), str(marker_log)]
        }
    }}, indent=2) + "\n")
    shutil.copy2(Path(__file__).parent / "marker_server.py", directory / ".agents/conformance/marker_server.py")
    hook_marker = directory / ".agents/conformance/hook_marker.py"
    write(hook_marker, f"""#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

log_path = Path({str(marker_log)!r})
marker = sys.argv[1] if len(sys.argv) > 1 else "native-hook"
payload = {{"marker": marker}}
if log_path.exists():
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if line and json.loads(line).get("marker") == marker:
            sys.exit(0)
with log_path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(payload) + "\\n")
""")
    session_marker_handler = {"type": "command", "command": f"python3 {hook_marker} native-session-hook"}
    prompt_marker_handler = {"type": "command", "command": f"python3 {hook_marker} native-prompt-hook"}
    tool_marker_handler = {"type": "command", "command": f"python3 {hook_marker} native-hook"}
    write(directory / ".agents/hooks/hooks.json", json.dumps({"hooks": {
        "SessionStart": [{"hooks": [session_marker_handler]}],
        "UserPromptSubmit": [{"hooks": [prompt_marker_handler]}],
        "PreToolUse": [{
            "matcher": "mcp__oda-marker__record|oda-marker.record|record",
            "hooks": [tool_marker_handler],
        }],
    }}, indent=2) + "\n")
    write(directory / ".agents/skills/conformance-skill/SKILL.md", """---
name: conformance-skill
description: Use when a prompt contains ODA_SKILL_CONFORMANCE.
---

Call the `oda-marker` tool named `record` exactly once with marker
`portable-skill`.
""")
    subprocess.run(["git", "init", "-q"], cwd=directory, check=True)


def harness_executable(vendor: str) -> tuple[str | None, str]:
    env_name = HARNESS_BIN_ENV[vendor]
    configured = os.environ.get(env_name)
    if configured:
        return shutil.which(configured), env_name
    return shutil.which(vendor), env_name


def command(vendor: str, executable: str, directory: Path, prompt: str) -> list[str]:
    if vendor == "copilot":
        return [executable, "-C", str(directory), "--no-auto-update", "--allow-all", "--output-format", "json", "-p", prompt]
    if vendor == "codex":
        return [
            executable, "exec", "-C", str(directory), "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox", "--dangerously-bypass-hook-trust",
            "--ephemeral", "--json", prompt,
        ]
    return [executable, "-p", "--dangerously-skip-permissions", "--output-format", "stream-json", "--verbose", prompt]


def redact(output: str) -> str:
    for names in ACCEPTED_CREDENTIALS.values():
        for name in names:
            value = os.environ.get(name)
            if value:
                output = output.replace(value, "[REDACTED]")
    return output


def native_mcp_markers(vendor: str, output: str) -> list[str]:
    """Accept markers only from successful native MCP calls, never shell calls."""
    markers = []
    calls = {}
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if vendor == "codex" and event.get("type") == "item.completed":
            item = event.get("item", {})
            if (item.get("type") == "mcp_tool_call" and item.get("server") == "oda-marker"
                    and item.get("tool") == "record" and item.get("status") == "completed"
                    and not item.get("error") and not (item.get("result") or {}).get("isError")):
                arguments = item.get("arguments", {})
                if isinstance(arguments, str):
                    try: arguments = json.loads(arguments)
                    except ValueError: continue
                if isinstance(arguments, dict) and isinstance(arguments.get("marker"), str):
                    markers.append(arguments["marker"])
        if vendor == "claude":
            message = event.get("message", {})
            for block in message.get("content", []) if isinstance(message, dict) else []:
                if block.get("type") == "tool_use" and block.get("name") == "mcp__oda-marker__record":
                    calls[block.get("id")] = block.get("input", {}).get("marker")
                if block.get("type") == "tool_result" and not block.get("is_error"):
                    marker = calls.get(block.get("tool_use_id"))
                    if isinstance(marker, str): markers.append(marker)
        if vendor == "copilot":
            data = event.get("data", {})
            if event.get("type") == "tool.execution_start":
                name = data.get("toolName", "")
                if re.fullmatch(r"(?:mcp__)?oda[-_]marker[-_./:]+record", name):
                    calls[data.get("toolCallId")] = data.get("arguments", {}).get("marker")
            if event.get("type") == "tool.execution_complete" and data.get("success") is True:
                marker = calls.get(data.get("toolCallId"))
                if isinstance(marker, str): markers.append(marker)
    return markers


def bounded_transcript(output: str) -> str:
    # Omit internal model-state and reasoning blobs so useful native events
    # remain visible in the bounded evidence transcript.
    lines = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
            kind = event.get("type", "")
            if kind not in {"assistant.message", "tool.execution_start", "tool.execution_complete",
                            "session.error", "error", "item.completed", "turn.failed"}:
                continue
            if kind == "item.completed" and event.get("item", {}).get("type") == "reasoning":
                continue
        except (ValueError, AttributeError):
            pass
        try:
            event = json.loads(line)
            if isinstance(event.get("data"), dict):
                for key in ("reasoningOpaque", "reasoningText", "reasoningBlocks"):
                    event["data"].pop(key, None)
            line = json.dumps(event)
        except (ValueError, AttributeError):
            pass
        lines.append(line)
    return redact("\n".join(lines)[-20000:])


def credential_check_id(vendor: str) -> str:
    return f"preflight.{vendor}.credential"


def available_credentials(vendor: str) -> list[str]:
    return [name for name in ACCEPTED_CREDENTIALS[vendor] if os.environ.get(name)]


def accepted_credentials_label(vendor: str) -> str:
    return ",".join(ACCEPTED_CREDENTIALS[vendor])


def legacy_credential_check_id(vendor: str) -> str:
    # Keep stable check IDs for existing evidence where one canonical token was
    # already documented before multi-source credential checks were added.
    primary = ACCEPTED_CREDENTIALS[vendor][1] if vendor == "copilot" else ACCEPTED_CREDENTIALS[vendor][0]
    return f"preflight.{primary.lower().replace('_', '.')}"


def matches_pinned_version(output: str, expected: str) -> bool:
    return re.search(r"(?<![0-9A-Za-z.])" + re.escape(expected) + r"(?![0-9A-Za-z+-]|\.[0-9A-Za-z])", output) is not None


def version_command(executable: str) -> list[str]:
    return [executable, "--version"]


def read_jsonc(path: Path) -> dict[str, object]:
    # Copilot stores comments and can use trailing commas in config.json.
    token = r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/|,\s*(?=[}\]])'
    text = re.sub(token, lambda match: match[0] if match[0].startswith('"') else ' ',
                  path.read_text(encoding="utf-8-sig"), flags=re.DOTALL)
    document = json.loads(text)
    if not isinstance(document, dict): raise ValueError("native config must be an object")
    return document


def harness_environment(vendor: str, executable: str, metadata: dict[str, object], directory: Path) -> dict[str, str]:
    environment = dict(os.environ)
    if metadata.get("authMode") == "existing-login":
        for name in ACCEPTED_CREDENTIALS[vendor]: environment.pop(name, None)
    if metadata.get("authMode") == "existing-login" or vendor == "copilot":
        variable = "CODEX_HOME" if vendor == "codex" else "COPILOT_HOME"
        default = ".codex" if vendor == "codex" else ".copilot"
        source = Path(environment.get(variable, str(Path.home() / default)))
        runtime = directory / (vendor + "-runtime")
        runtime.mkdir(mode=0o700)
        # Native clients read their own saved authentication. Do not extract or
        # print tokens, and do not modify the user's login or session files.
        filename = "auth.json" if vendor == "codex" else "config.json"
        if metadata.get("authMode") == "existing-login" and (source / filename).is_file():
            shutil.copyfile(source / filename, runtime / filename)
            (runtime / filename).chmod(0o600)
        if vendor == "copilot":
            config_path = runtime / "config.json"
            config = read_jsonc(config_path) if config_path.exists() else {}
            config["trustedFolders"] = [str(directory / "repository"), str(directory / "disabled-repository")]
            config.pop("hooks", None)
            config["disableAllHooks"] = False
            config_path.write_text(json.dumps(config) + "\n")
            config_path.chmod(0o600)
            metadata["workspaceTrust"] = "temporary-fixture-folders"
        environment[variable] = str(runtime)
        metadata["runtimeHome"] = "temporary"
    if vendor == "claude":
        runtime = directory / "claude-runtime"
        runtime.mkdir(mode=0o700)
        environment["CLAUDE_CONFIG_DIR"] = str(runtime)
        metadata["runtimeHome"] = "temporary"
    if vendor == "codex" and metadata.get("credentialEnv") == "CODEX_ACCESS_TOKEN":
        codex_home = directory / "codex-home"
        codex_home.mkdir()
        environment["CODEX_HOME"] = str(codex_home)
        subprocess.run(
            [executable, "login", "--with-access-token"],
            input=os.environ["CODEX_ACCESS_TOKEN"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
            env=environment,
        )
        metadata["credentialImport"] = "codex.login.with-access-token"
    return environment


def source_metadata() -> dict[str, object]:
    metadata: dict[str, object] = {}
    metadata["sourceCommits"] = {}
    metadata["sourceDirty"] = {}
    repository = Path(__file__).resolve().parents[2]
    for component in (".", "CLI", "SPEC", "WORKBENCH"):
        path = repository / component
        revision = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True)
        status = subprocess.run(["git", "-C", str(path), "status", "--porcelain"], capture_output=True, text=True)
        metadata["sourceCommits"][component] = revision.stdout.strip() if revision.returncode == 0 else "unknown"
        metadata["sourceDirty"][component] = status.returncode != 0 or bool(status.stdout)
    metadata["platform"] = os.uname().sysname + " " + os.uname().machine
    return metadata


def preflight(vendor: str, auth: str = "environment") -> tuple[list[dict[str, object]], dict[str, str]]:
    checks: list[dict[str, object]] = []
    metadata: dict[str, str] = {}
    agents = os.environ.get("AGENTS_BIN")
    checks.append({"id": "preflight.agents.bin", "passed": bool(agents)})
    if agents:
        metadata["agentsBin"] = agents
        if Path(agents).is_file(): metadata["agentsSha256"] = hashlib.sha256(Path(agents).read_bytes()).hexdigest()
    executable, binary_env = harness_executable(vendor)
    metadata["harnessBinaryEnv"] = binary_env
    checks.append({"id": f"preflight.{vendor}.installed", "passed": executable is not None})
    if executable:
        metadata["harnessPath"] = executable
        completed = subprocess.run(version_command(executable), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
        version_output = completed.stdout.strip()
        metadata["harnessVersionOutput"] = version_output
        expected_version = str(VERSIONS["harnesses"][vendor]["version"])
        checks.append({
            "id": f"preflight.{vendor}.version",
            "passed": completed.returncode == 0 and matches_pinned_version(version_output, expected_version),
        })
    else:
        checks.append({"id": f"preflight.{vendor}.version", "passed": False})
    credentials = available_credentials(vendor)
    metadata["acceptedCredentialEnv"] = accepted_credentials_label(vendor)
    if credentials:
        metadata["credentialEnv"] = credentials[0]
    if auth == "auto":
        auth = "environment" if credentials or vendor == "claude" else "existing-login"
    metadata["authMode"] = auth
    if auth == "existing-login":
        metadata.pop("credentialEnv", None)
        if vendor not in {"codex", "copilot"}:
            raise ValueError("existing-login is available for Codex and Copilot only")
        # Copilot has no non-interactive login-status command. Its first native
        # request verifies authentication; preflight records only the mode.
        metadata["authenticationVerification"] = "native-request-required"
        checks.append({"id": f"preflight.{vendor}.existing-login", "passed": True})
        if vendor == "codex" and executable:
            status = subprocess.run([executable, "login", "status"], capture_output=True, text=True)
            checks.append({"id": "preflight.codex.login-status", "passed": status.returncode == 0})
    else:
        checks.append({"id": legacy_credential_check_id(vendor), "passed": bool(credentials)})
        checks.append({"id": credential_check_id(vendor), "passed": bool(credentials)})
    return checks, metadata


def write_evidence(args: argparse.Namespace, passed: bool, checks: list[dict[str, object]], metadata: dict[str, object], run_mode: str) -> None:
    package = VERSIONS["harnesses"][args.vendor]
    evidence = {
        "schemaVersion": "1.0.0",
        "standardVersion": "1.0.0",
        "implementation": f"reference-cli-{args.vendor}",
        "implementationVersion": os.environ.get("ODA_VERSION", "dev"),
        "class": "adapter",
        "passed": passed,
        "checks": checks,
        "metadata": {
            "harness": args.vendor,
            "runMode": run_mode,
            "package": package,
            "runnerSha256": RUNNER_SHA256,
            "platform": os.uname().sysname + " " + os.uname().machine,
            "testedAt": datetime.now(UTC).isoformat(),
            **metadata,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")


def disabled_hook_case(vendor: str, agents: str, executable: str, directory: Path,
                       environment: dict[str, str]) -> dict[str, object]:
    """Require a successful MCP call with no hook marker after deactivation."""
    directory.mkdir()
    fixture(directory)
    subprocess.run([agents, "apply", "--vendor", vendor, "--root", str(directory)], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    catalogue = directory / ".agents/hooks/hooks.json"
    document = json.loads(catalogue.read_text())
    document["disableAllHooks"] = True
    catalogue.write_text(json.dumps(document) + "\n")
    mode = "catalogue-disabled"
    if vendor != "copilot":
        # A rejected projection leaves the old active hooks in place. Verify
        # refusal, then use profile removal to deactivate only owned hooks.
        before = {str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob("*")
                  if p.is_file() and ".git" not in p.parts}
        refusal = subprocess.run([agents, "apply", "--vendor", vendor, "--root", str(directory)],
                                 text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        after = {str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob("*")
                 if p.is_file() and ".git" not in p.parts}
        if refusal.returncode == 0 or "ODA-HOOK-0001" not in refusal.stdout or before != after:
            return {"passed": False, "mode": "refusal", "output": redact(refusal.stdout[-20000:])}
        manifest = directory / ".agents/manifest.json"
        document = json.loads(manifest.read_text())
        document["profiles"].remove("hooks")
        manifest.write_text(json.dumps(document) + "\n")
        mode = "profile-removal"
    subprocess.run([agents, "apply", "--vendor", vendor, "--root", str(directory)], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    completed = subprocess.run(command(vendor, executable, directory,
                               "ODA_ROOT_CONFORMANCE. Follow the applicable repository instructions."),
                               cwd=directory, text=True, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, timeout=600, env=environment)
    marker_path = directory / ".agents/conformance/markers.jsonl"
    markers = [json.loads(line)["marker"] for line in marker_path.read_text().splitlines() if line] if marker_path.exists() else []
    native_markers = native_mcp_markers(vendor, completed.stdout)
    passed = (completed.returncode == 0 and "root-instruction" in native_markers and "root-instruction" in markers
              and not any(marker in markers for marker in ("native-hook", "native-session-hook", "native-prompt-hook")))
    return {"passed": passed, "mode": mode, "markers": markers,
            "returncode": completed.returncode, "nativeMcpMarkers": native_markers, "output": bounded_transcript(completed.stdout)}


def run() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("vendor", choices=("copilot", "codex", "claude"))
    parser.add_argument("--auth", choices=("auto", "environment", "existing-login"), default="auto")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--preflight-only", action="store_true", help="write harness preflight evidence without running the agent")
    args = parser.parse_args()
    preflight_checks, preflight_metadata = preflight(args.vendor, args.auth)
    preflight_metadata.update(source_metadata())
    preflight_passed = all(bool(check["passed"]) for check in preflight_checks)
    if args.preflight_only:
        write_evidence(args, preflight_passed, preflight_checks, preflight_metadata, "preflight")
        return 0 if preflight_passed else 1
    if not preflight_passed:
        write_evidence(args, False, preflight_checks, preflight_metadata, "native")
        return 2
    agents = os.environ["AGENTS_BIN"]

    try:
        with tempfile.TemporaryDirectory(prefix=f"oda-{args.vendor}-") as temporary:
            repository = Path(temporary) / "repository"
            repository.mkdir()
            fixture(repository)
            subprocess.run([agents, "apply", "--vendor", args.vendor, "--root", str(repository)], check=True)
            executable = preflight_metadata["harnessPath"]
            harness_env = harness_environment(args.vendor, executable, preflight_metadata, Path(temporary))
            cases = [
                {
                    "case": "root-instruction",
                    "cwd": repository,
                    "expectedMarker": "root-instruction",
                    "prompt": "ODA_ROOT_CONFORMANCE. Follow the applicable repository instructions.",
                },
                {
                    "case": "nested-instruction",
                    "cwd": repository / "packages/api",
                    "expectedMarker": "nested-instruction",
                    "prompt": "ODA_NESTED_CONFORMANCE. Follow the nearest applicable repository instructions.",
                },
                {
                    "case": "portable-skill",
                    "cwd": repository,
                    "expectedMarker": "portable-skill",
                    "prompt": "ODA_SKILL_CONFORMANCE. Use the applicable skill exactly.",
                },
            ]
            transcripts = []
            for case in cases:
                cwd = case["cwd"]
                completed = subprocess.run(command(args.vendor, executable, cwd, case["prompt"]), cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600, env=harness_env)
                transcripts.append({
                    "case": case["case"],
                    "expectedMarker": case["expectedMarker"],
                    "cwd": str(cwd.relative_to(repository)),
                    "command": command(args.vendor, executable, cwd, case["prompt"]),
                    "returncode": completed.returncode,
                    "output": bounded_transcript(completed.stdout),
                    "nativeMcpMarkers": native_mcp_markers(args.vendor, completed.stdout),
                })
                if completed.returncode != 0:
                    break
            marker_path = repository / ".agents/conformance/markers.jsonl"
            markers = []
            if marker_path.exists():
                markers = [json.loads(line)["marker"] for line in marker_path.read_text().splitlines() if line]
            version = subprocess.run(version_command(executable), text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=harness_env).stdout.strip()
            disabled = disabled_hook_case(args.vendor, agents, executable,
                                          Path(temporary) / "disabled-repository", harness_env) if all(t["returncode"] == 0 for t in transcripts) else {"passed": False, "mode": "not-run", "reason": "enabled native request failed"}
            passed = disabled["passed"] and all(marker in markers for marker in EXPECTED_MARKERS) and all(item["returncode"] == 0 and item["expectedMarker"] in item["nativeMcpMarkers"] for item in transcripts)
            checks = preflight_checks + [
                {"id": MARKER_CHECKS[name], "passed": name in markers}
                for name in EXPECTED_MARKERS
            ]
            checks.append({"id": "native.mcp.stdio", "passed": len(transcripts) == 3 and all(t["expectedMarker"] in t["nativeMcpMarkers"] for t in transcripts)})
            checks.append({"id": "native.hook.disabled", "passed": disabled["passed"]})
            metadata = {
                **preflight_metadata,
                "disabledHooks": disabled,
                "harnessVersion": version,
                "markers": markers,
                "transcripts": transcripts,
            }
            write_evidence(args, passed, checks, metadata, "native")
            return 0 if passed else 1
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        checks = preflight_checks + [{"id": "native.runtime", "passed": False,
                                      "message": redact(str(error))}]
        write_evidence(args, False, checks, preflight_metadata, "native")
        return 1



if __name__ == "__main__":
    raise SystemExit(run())
