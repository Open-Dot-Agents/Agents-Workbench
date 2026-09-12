#!/usr/bin/env python3
import copy
import json
import io
from contextlib import redirect_stdout
import unittest
from unittest import mock

from verify_copilot_local_network import RESULT, verify, verify_record


class EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.record = json.loads(RESULT.read_text())

    def test_valid(self): verify_record(copy.deepcopy(self.record))

    def test_matching_current_files_cannot_replace_missing_snapshot(self):
        output = io.StringIO()
        with mock.patch('verify_copilot_local_network.sha', return_value=self.record['runner_sha256']), redirect_stdout(output):
            verify()
        report = json.loads(output.getvalue())
        self.assertTrue(report['current_sources_match'])
        self.assertFalse(report['historical_integrity'])
        self.assertFalse(report['current_support_eligible'])

    def test_loopback_denial_claim_refused(self):
        record = copy.deepcopy(self.record); record["observations"]["self-loopback"]["outcome"] = "denied"
        with self.assertRaises(AssertionError): verify_record(record)

    def test_abstract_denial_claim_refused(self):
        record = copy.deepcopy(self.record); record["observations"]["self-abstract-unix"]["outcome"] = "denied"
        with self.assertRaises(AssertionError): verify_record(record)

    def test_enabled_local_network_refused(self):
        record = copy.deepcopy(self.record); record["local_network_mismatch_observed"] = False
        with self.assertRaises(AssertionError): verify_record(record)

    def test_uncorrelated_execution_refused(self):
        record = copy.deepcopy(self.record)
        for event in record["native_events"]:
            if event.get("type") == "tool.execution_complete": event["data"]["toolCallId"] = "other"
        with self.assertRaises(AssertionError): verify_record(record)

    def test_host_socket_access_refused(self):
        record = copy.deepcopy(self.record); record["host_unix_marker_received"] = True
        with self.assertRaises(AssertionError): verify_record(record)


if __name__ == "__main__": unittest.main()
