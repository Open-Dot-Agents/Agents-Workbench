#!/usr/bin/env python3
"""Reject instruction evidence with missing effects or changed scope."""
import json
import unittest

from verify_copilot_agent_instructions import BASE, check_phase


class EvidenceTests(unittest.TestCase):
    def result(self, label='combined-root'):
        return json.loads((BASE/f'copilot-agent-instructions-{label}-user-instructions.json').read_text())

    def test_valid(self):
        r = self.result()
        for phase in r['phases']: check_phase(phase, r)

    def test_reference_loss(self):
        r = self.result()
        phase = r['phases'][0]
        for message in phase['requests'][0]['messages']:
            if message['role'] == 'system' and isinstance(message.get('content'), str):
                message['content'] = message['content'].replace('AGENTS_CHILD_REFERENCED_POLICY', '')
        with self.assertRaises(AssertionError): check_phase(phase, r)

    def test_wrong_scope(self):
        r = self.result('dot-root')
        r['cwd_subdir'] = '.claude'
        with self.assertRaises(AssertionError): check_phase(r['phases'][0], r)

    def test_missing_effect(self):
        r = self.result()
        r['phases'][0]['native_events'] = [e for e in r['phases'][0]['native_events'] if e['type'] != 'tool.execution_complete']
        with self.assertRaises(AssertionError): check_phase(r['phases'][0], r)


if __name__ == '__main__':
    unittest.main()
