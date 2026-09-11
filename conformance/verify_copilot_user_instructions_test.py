#!/usr/bin/env python3
"""Reject false update, removal, reference, and execution claims."""
import json
import unittest

from verify_copilot_user_instructions import BASE, check_phase


class EvidenceTests(unittest.TestCase):
    def phase(self, index=2):
        return json.loads((BASE/'copilot-user-instructions-custom-source-final.json').read_text())['phases'][index]

    def test_valid(self):
        for index in range(4): check_phase(self.phase(index))

    def test_stale_update(self):
        phase=self.phase(1); phase['label']='updated'
        with self.assertRaises(AssertionError): check_phase(phase)

    def test_false_removal(self):
        phase=self.phase(1); phase['label']='removed'
        with self.assertRaises(AssertionError): check_phase(phase)

    def test_wrong_reference_base(self):
        phase=self.phase()
        for message in phase['requests'][0]['messages']:
            if message['role']=='system' and isinstance(message.get('content'),str):
                message['content']=message['content'].replace('AGENTS_USER_REFERENCE','AGENTS_PROJECT_REFERENCE_DECOY')
        with self.assertRaises(AssertionError): check_phase(phase)

    def test_missing_effect(self):
        phase=self.phase(); phase['native_events']=[e for e in phase['native_events'] if e['type']!='tool.execution_complete']
        with self.assertRaises(AssertionError): check_phase(phase)


if __name__=='__main__':
    unittest.main()
