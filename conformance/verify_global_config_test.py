#!/usr/bin/env python3
import json
import unittest

from verify_global_config import BASE, RECEIPTS, verify_record


class EvidenceTests(unittest.TestCase):
    path = BASE/RECEIPTS['codex']

    def record(self): return json.loads(self.path.read_text())

    def test_valid(self): verify_record(self.record(), self.path)

    def test_missing_global_instructions(self):
        record = self.record()
        record['phases'][0]['model_requests'] = json.loads(json.dumps(record['phases'][0]['model_requests']).replace('AGENTS_GLOBAL_CORE', 'MISSING'))
        with self.assertRaises(AssertionError): verify_record(record, self.path)

    def test_override_did_not_take_effect(self):
        record = self.record()
        record['phases'][1]['effective_config']['config']['model'] = 'global-model'
        with self.assertRaises(AssertionError): verify_record(record, self.path)

    def test_project_changed_user_files(self):
        record = self.record()
        record['user_after_project']['config.toml'] = 'changed'
        with self.assertRaises(AssertionError): verify_record(record, self.path)

    def test_adapter_changed_authority(self):
        record = self.record()
        next(row for row in record['commands'] if row['command'][0].endswith('/agents'))['authority_after'] = {}
        with self.assertRaises(AssertionError): verify_record(record, self.path)

    def test_uncorrelated_native_output(self):
        record = self.record()
        record['phases'][0]['events'] = []
        with self.assertRaises(AssertionError): verify_record(record, self.path)


if __name__ == '__main__': unittest.main()
