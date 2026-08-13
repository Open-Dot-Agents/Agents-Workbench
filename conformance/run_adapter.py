#!/usr/bin/env python3
"""Run version-pinned native harness acceptance and emit durable JSON evidence."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKBENCH = Path(__file__).resolve().parents[1]
VERSIONS = json.loads((Path(__file__).parent / "versions.json").read_text())


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def fixture(directory: Path) -> None:
    write(directory / ".agents/AGENTS.md", """# Root conformance instructions

When the prompt contains `ODA_ROOT_CONFORMANCE`, call the `oda-marker` tool
named `record` exactly once with marker `root-instruction`.
""")
    (directory / "AGENTS.md").symlink_to(".agents/AGENTS.md")
    write(directory / "packages/api/AGENTS.md", """# Nested conformance instructions

When the prompt contains `ODA_NESTED_CONFORMANCE`, call the `oda-marker` tool
named `record` exactly once with marker `nested-instruction`.
""")
    write(directory / ".agents/manifest.json", json.dumps({
        "version": "1.0.0", "profiles": ["tools", "skills"]
    }, indent=2) + "\n")
    marker_server = directory / ".agents/conformance/marker_server.py"
    marker_log = directory / ".agents/conformance/markers.jsonl"
    write(directory / ".agents/tools/mcp.json", json.dumps({"mcpServers": {
        "oda-marker": {
            "type": "stdio", "command": "python3",
            "args": [str(marker_server), str(marker_log)]
        }
    }}, indent=2) + "\n")
    shutil.copy2(Path(__file__).parent / "marker_server.py", directory / ".agents/conformance/marker_server.py")
    write(directory / ".agents/skills/conformance-skill/SKILL.md", """---
name: conformance-skill
description: Use when a prompt contains ODA_SKILL_CONFORMANCE.
---

Call the `oda-marker` tool named `record` exactly once with marker
`portable-skill`.
""")
    subprocess.run(["git", "init", "-q"], cwd=directory, check=True)


def command(vendor: str, directory: Path, prompt: str) -> list[str]:
    if vendor == "copilot":
        return ["copilot", "-C", str(directory), "--no-auto-update", "--allow-all-tools", "--output-format", "json", "-p", prompt]
    if vendor == "codex":
        return ["codex", "exec", "-C", str(directory), "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox", "--ephemeral", "--json", prompt]
    return ["claude", "-p", "--dangerously-skip-permissions", "--output-format", "stream-json", "--verbose", prompt]


def redact(output: str) -> str:
    for name in ("GH_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        value = os.environ.get(name)
        if value:
            output = output.replace(value, "[REDACTED]")
    return output


def run() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("vendor", choices=("copilot", "codex", "claude"))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    agents = os.environ.get("AGENTS_BIN")
    if not agents:
        raise SystemExit("AGENTS_BIN is required")
    expected_credential = {"copilot": "GH_TOKEN", "codex": "OPENAI_API_KEY", "claude": "ANTHROPIC_API_KEY"}[args.vendor]
    if not os.environ.get(expected_credential):
        raise SystemExit(f"{expected_credential} is required for native evidence")

    with tempfile.TemporaryDirectory(prefix=f"oda-{args.vendor}-") as temporary:
        repository = Path(temporary) / "repository"
        repository.mkdir()
        fixture(repository)
        subprocess.run([agents, "apply", "--vendor", args.vendor, "--root", str(repository)], check=True)
        cases = [
            (repository, "ODA_ROOT_CONFORMANCE. Follow the applicable repository instructions."),
            (repository / "packages/api", "ODA_NESTED_CONFORMANCE. Follow the nearest applicable repository instructions."),
            (repository, "ODA_SKILL_CONFORMANCE. Use the applicable skill exactly."),
        ]
        transcripts = []
        for cwd, prompt in cases:
            completed = subprocess.run(command(args.vendor, cwd, prompt), cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
            transcripts.append({"cwd": str(cwd.relative_to(repository)), "returncode": completed.returncode, "output": redact(completed.stdout[-20000:])})
            if completed.returncode != 0:
                break
        marker_path = repository / ".agents/conformance/markers.jsonl"
        markers = []
        if marker_path.exists():
            markers = [json.loads(line)["marker"] for line in marker_path.read_text().splitlines() if line]
        expected = ["root-instruction", "nested-instruction", "portable-skill"]
        package = VERSIONS["harnesses"][args.vendor]
        version = subprocess.run([args.vendor if args.vendor != "claude" else "claude", "--version"], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout.strip()
        passed = markers == expected and all(item["returncode"] == 0 for item in transcripts)
        evidence = {
            "schemaVersion": "1.0.0",
            "standardVersion": "1.0.0",
            "implementation": f"reference-cli-{args.vendor}",
            "implementationVersion": os.environ.get("ODA_VERSION", "dev"),
            "class": "adapter",
            "passed": passed,
            "checks": [{"id": f"native.{name}", "passed": name in markers} for name in expected],
            "metadata": {
                "harness": args.vendor,
                "harnessVersion": version,
                "package": package,
                "platform": os.uname().sysname + " " + os.uname().machine,
                "testedAt": datetime.now(UTC).isoformat(),
                "markers": markers,
                "transcripts": transcripts,
            },
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(run())
