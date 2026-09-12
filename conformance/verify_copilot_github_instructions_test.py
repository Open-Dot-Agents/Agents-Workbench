#!/usr/bin/env python3
"""Reject missing, stale, or unrelated native instruction evidence."""
import unittest

from synthetic_instruction_fixtures import instruction_phase

from verify_copilot_root_instructions import check_phase


class EvidenceTests(unittest.TestCase):
    def phase(self, index=1):
        bodies = (
            ['AGENTS_ROOT_INSTRUCTION_MARKER', 'AGENTS_WRONG_REFERENCE_BASE'],
            ['AGENTS_ROOT_INSTRUCTION_MARKER', 'AGENTS_WRONG_REFERENCE_BASE'],
            ['AGENTS_ROOT_INSTRUCTION_MARKER', 'AGENTS_WRONG_REFERENCE_BASE', 'AGENTS_UPDATED_GITHUB_MARKER'],
            [],
        )
        return instruction_phase(bodies[index], ('source', 'relocated', 'updated', 'removed')[index])

    def test_valid_phases(self):
        for index in range(4): check_phase(self.phase(index), 'github-reference')

    def test_wrong_reference_base(self):
        phase = self.phase()
        for message in phase['requests'][0]['messages']:
            if message['role'] == 'system' and isinstance(message.get('content'), str):
                message['content'] = message['content'].replace('AGENTS_WRONG_REFERENCE_BASE', 'AGENTS_REFERENCED_POLICY')
        with self.assertRaises(AssertionError): check_phase(phase, 'github-reference')

    def test_removed_body_still_loaded(self):
        phase = self.phase()
        phase['label'] = 'removed'
        with self.assertRaises(AssertionError): check_phase(phase, 'github-reference')

    def test_stale_update(self):
        phase = self.phase()
        phase['label'] = 'updated'
        with self.assertRaises(AssertionError): check_phase(phase, 'github-reference')

    def test_wrong_session(self):
        phase = self.phase()
        phase['session']['sessionId'] = 'unrelated'
        with self.assertRaises(AssertionError): check_phase(phase, 'github-reference')

    def test_missing_observable_read(self):
        phase = self.phase()
        phase['native_events'] = [e for e in phase['native_events'] if e['type'] != 'tool.execution_complete']
        with self.assertRaises(AssertionError): check_phase(phase, 'github-reference')


if __name__ == '__main__':
    unittest.main()
