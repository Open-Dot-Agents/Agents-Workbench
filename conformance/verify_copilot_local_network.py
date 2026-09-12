#!/usr/bin/env python3
"""Verify the pinned Copilot local-network class limitation evidence."""
import json
from pathlib import Path

from run_native_approvals import PINS, sha

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "WORKBENCH/evidence/2026-09-10-copilot-local-classes"
RESULT = BASE / "result.json"


def verify_record(record):
    assert record["evidence_class"] == "native-settings-assessment"
    assert not record["portable_projection_tested"] and not record["full_adapter_support"]
    assert record["copilot_sha256"] == PINS["copilot"]
    assert isinstance(record["runner_sha256"], str) and len(record["runner_sha256"]) == 64
    assert record["returncode"] == 0 and record["probe_unchanged"] and record["correlated_native_execution"]
    settings = json.loads((BASE / "settings.json").read_text())
    network = settings["sandbox"]["userPolicy"]["network"]
    assert network == {"allowOutbound": False, "allowLocalNetwork": False}
    assert record["deny_socket_path"] and record["host_tcp_reachable"]
    assert record["host_unix_reachable"] and record["host_abstract_reachable"]
    assert not record["host_unix_marker_received"] and not record["host_abstract_marker_received"]
    observations = record["observations"]
    denied = {"network-1", "network-2", "host-abstract-unix"}
    allowed = {"self-abstract-unix", "self-loopback", "self-loopback-ipv6", "self-loopback-udp"}
    assert all(observations[name]["outcome"] == "denied" for name in denied)
    assert all(observations[name]["outcome"] == "allowed" for name in allowed)
    assert record["local_network_mismatch_observed"] and not record["host_temporary_marker_created"]
    executions = {event.get("data", {}).get("toolCallId") for event in record["native_events"]
                  if event.get("type") == "tool.execution_start" and "probe.py" in json.dumps(event)}
    assert executions
    assert any(event.get("type") == "tool.execution_complete" and
               event.get("data", {}).get("toolCallId") in executions and
               event.get("data", {}).get("success") is True for event in record["native_events"])


def verify():
    verify_record(json.loads(RESULT.read_text()))
    current = json.loads(RESULT.read_text())["runner_sha256"] == sha(ROOT / "WORKBENCH/conformance/run_copilot_security.py")
    current = current and sha(BASE / "probe.py") == sha(ROOT / "WORKBENCH/conformance/copilot_security_probe.py")
    print(json.dumps({"passed": True, "historical_integrity": False,
                      "integrity_errors": ["captured runner snapshot is absent"],
                      "current_sources_match": current,
                      "current_support_eligible": False, "full_adapter_support": False}, indent=2))


if __name__ == "__main__": verify()
