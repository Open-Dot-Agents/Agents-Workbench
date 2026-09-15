#!/usr/bin/env python3
import copy
import tempfile
import unittest

from verify_public_github_mcp import ROOT, verify_copilot, verify_direct, verify_native
from synthetic_verifier_fixtures import snapshot, public_mcp_records


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = snapshot(directory.name)
        self.direct, self.native, self.copilot = public_mcp_records(self.path, ROOT)

    def test_valid(self):
        verify_direct(copy.deepcopy(self.direct), self.path)
        verify_native(copy.deepcopy(self.native), copy.deepcopy(self.direct), self.path)
        verify_copilot(copy.deepcopy(self.copilot), copy.deepcopy(self.direct), self.path)

    def test_direct_mutation_refused(self):
        record = copy.deepcopy(self.direct); record["requests"][1]["method"] = "tools/call"
        with self.assertRaises(AssertionError): verify_direct(record, self.path)

    def test_tool_loss_refused(self):
        record = copy.deepcopy(self.native); record["github_tool_names"].pop()
        with self.assertRaises(AssertionError): verify_native(record, self.direct, self.path)

    def test_schema_loss_refused(self):
        record = copy.deepcopy(self.native)
        namespace = next(tool for tool in record["model_requests"][0]["tools"] if tool.get("name") == "mcp__github")
        namespace["tools"][0].pop("parameters")
        with self.assertRaises(AssertionError): verify_native(record, self.direct, self.path)

    def test_uncorrelated_ready_refused(self):
        record = copy.deepcopy(self.native); record["terminal_mcp_startup"]["threadId"] = "other"
        with self.assertRaises(AssertionError): verify_native(record, self.direct, self.path)

    def test_tool_call_refused(self):
        record = copy.deepcopy(self.native)
        record["events"].append({"method":"item/completed","params":{"item":{"type":"mcpToolCall"}}})
        with self.assertRaises(AssertionError): verify_native(record, self.direct, self.path)

    def test_credential_declaration_refused(self):
        record = copy.deepcopy(self.native); record["credential_value_stored"] = True
        with self.assertRaises(AssertionError): verify_native(record, self.direct, self.path)

    def test_provenance_mutation_refused(self):
        record = copy.deepcopy(self.native); record["source_files"][".mcp.json"] = "0" * 64
        with self.assertRaises(AssertionError): verify_native(record, self.direct, self.path)

    def test_copilot_tool_loss_refused(self):
        record = copy.deepcopy(self.copilot); record["github_tool_names"].pop()
        with self.assertRaises(AssertionError): verify_copilot(record, self.direct, self.path)

    def test_copilot_trust_drift_refused(self):
        record = copy.deepcopy(self.copilot); record["native_trusted_folders"] = ["/other"]
        with self.assertRaises(AssertionError): verify_copilot(record, self.direct, self.path)

    def test_copilot_tool_call_refused(self):
        record = copy.deepcopy(self.copilot); record["events"].append({"tool_call":{"name":"github-get_me"}})
        with self.assertRaises(AssertionError): verify_copilot(record, self.direct, self.path)

    def test_copilot_credential_declaration_refused(self):
        record = copy.deepcopy(self.copilot); record["credential_sha256_stored"] = True
        with self.assertRaises(AssertionError): verify_copilot(record, self.direct, self.path)


if __name__ == "__main__": unittest.main()
