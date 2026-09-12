#!/usr/bin/env python3
"""Verify direct and native read-only public GitHub MCP discovery evidence."""
import json
from pathlib import Path

from evidence_state import summarize_receipts
from run_native_approvals import PINS, sha

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "WORKBENCH/evidence/native-draft2-debug"
DIRECT = BASE / "public-github-mcp-readonly-probe.json"
NATIVE = BASE / "native-public-github-plugin-shared-auth.json"
COPILOT = BASE / "native-public-github-plugin-copilot-final-complete.json"


def verify_direct(record, path=DIRECT):
    assert record["passed"] and record["tool_count"] == 47
    assert record["endpoint"] == "https://api.githubcopilot.com/mcp/"
    assert record["operation"] == "initialize and tools/list only"
    assert record["credential_source"] == "gh auth keyring"
    assert not record["credential_value_stored"] and not record["credential_sha256_stored"]
    assert not record["remote_mutations"]
    assert record["runner_sha256"] == sha(path.with_suffix(".runner.py"))
    names = record["tool_names"]
    assert names == sorted(names) and len(names) == len(set(names)) == 47 and "get_me" in names
    assert [request["method"] for request in record["requests"]] == ["initialize", "tools/list"]
    assert all(request["status"] == 200 and request["session_returned"] for request in record["requests"])


def verify_native(record, direct, path=NATIVE):
    assert record["passed"] and not record["full_adapter_support"]
    assert record["native_version"] == "0.154.0" and record["native_sha256"] == PINS["codex"]
    assert record["runner_sha256"] == sha(path.with_suffix(".runner.py"))
    assert record["public_endpoint"] == direct["endpoint"]
    assert record["credential_source"] == "gh auth keyring"
    assert not record["credential_value_stored"] and not record["credential_sha256_stored"]
    assert record["external_github_mcp"] and not record["external_model"] and not record["remote_mutations"]
    provenance = json.loads((ROOT / ".agents/plugins/com.openai.codex/provenance.json").read_text())
    assert record["source_revision"] == provenance["revision"]
    assert record["source_files"] == provenance["files"]
    source = ROOT / ".agents/plugins/com.openai.codex/plugins/github"
    assert all(sha(source / name) == digest for name, digest in record["source_files"].items())
    assert provenance["transformations"] == [{
        "path": ".mcp.json",
        "reason": "Add the Copilot-compatible Authorization environment reference while retaining the Codex bearer field.",
        "upstream_sha256": "730ebd45944d5f46aeded73c8fa8a2e5765726c626a8605671809d6159d31edd",
    }]
    installed = record["plugin_list"]["installed"]
    assert len(installed) == 1 and installed[0]["pluginId"] == "github@oda-public-github"
    assert installed[0]["installed"] and installed[0]["enabled"]
    assert record["github_namespace_count"] == 1 and record["github_tool_count"] == 47
    assert record["direct_tool_count"] == direct["tool_count"] and record["direct_tool_names_match"]
    assert record["github_tool_names"] == direct["tool_names"]
    namespaces = [tool for request in record["model_requests"] for tool in request.get("tools", [])
                  if tool.get("type") == "namespace" and tool.get("name") == "mcp__github"]
    assert len(namespaces) == 1
    tools = namespaces[0]["tools"]
    assert sorted(tool["name"] for tool in tools) == direct["tool_names"]
    assert all(tool.get("type") == "function" and isinstance(tool.get("parameters"), dict) for tool in tools)
    thread_id = record["thread"]["thread"]["id"]
    assert record["terminal_mcp_startup"] == record["mcp_ready_events"][0]
    assert record["terminal_mcp_startup"]["threadId"] == thread_id
    assert record["terminal_mcp_startup"]["name"] == "github" and record["terminal_mcp_startup"]["status"] == "ready"
    assert record["completion"]["params"]["threadId"] == thread_id
    assert record["completion"]["params"]["turn"]["status"] == "completed"
    assert "AGENTS_PUBLIC_GITHUB_READY" in json.dumps(record["completion"])
    assert not record["approvals"] and not record["provider_errors"]
    assert not any(event.get("method") in ("item/started", "item/completed") and
                   event.get("params", {}).get("item", {}).get("type") == "mcpToolCall"
                   for event in record["events"])


def verify_copilot(record, direct, path=COPILOT):
    assert record["passed"] and not record["full_adapter_support"]
    assert record["native_version"] == "1.0.83" and record["native_sha256"] == PINS["copilot"]
    assert record["runner_sha256"] == sha(path.with_suffix(".runner.py"))
    assert record["public_endpoint"] == direct["endpoint"] and record["external_github_mcp"]
    assert not record["external_model"] and not record["remote_mutations"]
    assert not record["credential_value_stored"] and not record["credential_sha256_stored"]
    provenance = json.loads((ROOT / ".agents/plugins/com.openai.codex/provenance.json").read_text())
    assert record["source_revision"] == provenance["revision"]
    assert record["source_files"] == provenance["files"] and record["source_transformations"] == provenance["transformations"]
    source = ROOT / ".agents/plugins/com.openai.codex/plugins/github"
    assert all(sha(source / name) == digest for name, digest in record["source_files"].items())
    assert "github@agents-public-github" in record["plugin_list"]["stdout"]
    assert record["github_tool_count"] == record["direct_tool_count"] == 47
    assert record["direct_tool_names_match"] and record["github_tool_names"] == direct["tool_names"]
    tools = [tool for request in record["model_requests"] for tool in request.get("tools", [])
             if tool.get("type") == "function" and tool.get("function", {}).get("name", "").startswith("github-")]
    assert sorted(tool["function"]["name"][len("github-"):] for tool in tools) == direct["tool_names"]
    assert all(isinstance(tool["function"].get("parameters"), dict) for tool in tools)
    assert record["completion"]["stopReason"] == "end_turn" and record["model_requests"]
    assert not record["approvals"] and not record["provider_errors"]
    assert record["native_config_changed"] and record["native_first_launch_recorded"]
    assert record["native_trusted_folders"] == [str(Path(record["fixture"]) / "workspace")]
    assert not any("tool_call" in json.dumps(event).lower() or "mcpToolCall" in json.dumps(event)
                   for event in record["events"])


def verify():
    direct = json.loads(DIRECT.read_text())
    native = json.loads(NATIVE.read_text())
    copilot = json.loads(COPILOT.read_text())
    verify_direct(direct)
    verify_native(native, direct)
    verify_copilot(copilot, direct)
    direct_state = summarize_receipts([DIRECT], ROOT,
                                      current_runner=ROOT/'WORKBENCH/conformance/probe_public_github_mcp.py')
    codex_state = summarize_receipts([NATIVE], ROOT,
                                     current_runner=ROOT/'WORKBENCH/conformance/run_native_public_github_plugin.py',
                                     current_helper=ROOT/'WORKBENCH/conformance/run_native_approvals.py')
    copilot_state = summarize_receipts([COPILOT], ROOT,
                                       current_runner=ROOT/'WORKBENCH/conformance/run_native_public_github_plugin_copilot.py',
                                       current_helper=ROOT/'WORKBENCH/conformance/run_native_approvals.py')
    states = {'direct': direct_state, 'codex': codex_state, 'copilot': copilot_state}
    assert all(state['historical_integrity'] for state in states.values()), states
    print(json.dumps({'passed': True, 'evidence': states,
                      'current_support_eligible': all(state['current_support_eligible'] for state in states.values()),
                      'full_adapter_support': False}, indent=2))


if __name__ == "__main__":
    verify()
