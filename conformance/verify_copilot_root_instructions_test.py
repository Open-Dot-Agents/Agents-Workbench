#!/usr/bin/env python3
"""Ensure that correlated native evidence is required for root instruction claims."""
import unittest

from synthetic_instruction_fixtures import instruction_phase

from verify_copilot_root_instructions import check_phase


class EvidenceTests(unittest.TestCase):
    def phase(self):
        return instruction_phase(['AGENTS_ROOT_INSTRUCTION_MARKER'])

    def test_valid_phase(self):
        check_phase(self.phase(), 'root')

    def test_missing_instruction_body(self):
        phase = self.phase()
        for message in phase['requests'][0]['messages']:
            if message['role'] == 'system' and isinstance(message.get('content'), str):
                message['content'] = message['content'].replace('AGENTS_ROOT_INSTRUCTION_MARKER', '')
        with self.assertRaises(AssertionError): check_phase(phase, 'root')

    def test_missing_read_effect(self):
        phase = self.phase()
        phase['native_events'] = [e for e in phase['native_events'] if e['type'] != 'tool.execution_complete']
        with self.assertRaises(AssertionError): check_phase(phase, 'root')

    def test_wrong_session(self):
        phase = self.phase()
        phase['session']['sessionId'] = 'unrelated'
        with self.assertRaises(AssertionError): check_phase(phase, 'root')

    def test_reference_base_cannot_be_relabelled(self):
        phase = instruction_phase(['AGENTS_ROOT_INSTRUCTION_MARKER', 'AGENTS_WRONG_REFERENCE_BASE'], 'unconverted')
        check_phase(phase, 'reference')
        phase['label'] = 'source'
        with self.assertRaises(AssertionError): check_phase(phase, 'reference')


if __name__ == '__main__':
    unittest.main()
