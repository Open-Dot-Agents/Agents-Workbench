#!/usr/bin/env python3
"""Reject stale or uncorrelated settings lifecycle evidence."""
import json
import unittest

from run_native_copilot_preferences import status_at_padding
from verify_copilot_settings import RECEIPT, verify_record


class EvidenceTests(unittest.TestCase):
    def record(self):
        return json.loads(RECEIPT.read_text())

    def test_valid_receipt(self):
        verify_record(self.record())

    def test_wrong_native_context(self):
        record = self.record()
        record['phases'][0]['status_events'][0]['value']['cwd'] = '/other'
        with self.assertRaises(AssertionError): verify_record(record)

    def test_stale_update(self):
        record = self.record()
        record['phases'][1]['projected_preferences'] = record['phases'][0]['projected_preferences']
        with self.assertRaises(AssertionError): verify_record(record)

    def test_removed_command_still_runs(self):
        record = self.record()
        record['phases'][2]['status_events'] = record['phases'][1]['status_events']
        with self.assertRaises(AssertionError): verify_record(record)

    def test_terminal_summary_requires_raw_evidence(self):
        record = self.record()
        record['phases'][0]['raw_output'] = ''
        with self.assertRaises(AssertionError): verify_record(record)

    def test_reused_session(self):
        record = self.record()
        identity = record['phases'][0]['status_events'][0]['value']['session_id']
        for event in record['phases'][1]['status_events']: event['value']['session_id'] = identity
        with self.assertRaises(AssertionError): verify_record(record)

    def test_status_padding_accepts_cursor_or_literal_columns(self):
        self.assertTrue(status_at_padding("\x1b[12;4HODA_STATUS_plan", "plan", 3))
        self.assertTrue(status_at_padding("\r\n   ODA_STATUS_plan", "plan", 3))
        self.assertFalse(status_at_padding("\r\n  ODA_STATUS_plan", "plan", 3))


if __name__ == '__main__':
    unittest.main()
