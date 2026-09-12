#!/usr/bin/env python3
import json
import unittest

from verify_copilot_sidekick import BASE, verify_record


class EvidenceTests(unittest.TestCase):
    path = BASE/'copilot-sidekick-user-ignored.json'

    def record(self): return json.loads(self.path.read_text())

    def test_valid(self): verify_record(self.record(), self.path)

    def test_warning_required(self):
        record = self.record()
        record['native_logs'] = {}
        with self.assertRaises(AssertionError): verify_record(record, self.path)

    def test_absence_alone_is_not_evidence(self):
        record = self.record()
        record['model_requests'] = []
        with self.assertRaises(AssertionError): verify_record(record, self.path)

    def test_effect_contradicts_limitation(self):
        record = self.record()
        record['effect'] = 'AGENTS_SIDEKICK_EFFECT'
        with self.assertRaises(AssertionError): verify_record(record, self.path)


if __name__ == '__main__': unittest.main()
