#!/usr/bin/env python3
"""Reject stale core-loading or changed reference-base claims."""
import unittest

from synthetic_instruction_fixtures import instruction_phase

from verify_copilot_canonical_instructions import check_canonical_phase


class EvidenceTests(unittest.TestCase):
    def phase(self, index=2):
        markers = ['AGENTS_ROOT_INSTRUCTION_MARKER', 'AGENTS_OTHER_INSTRUCTION_MARKER',
                   'AGENTS_REFERENCED_POLICY', 'AGENTS_NATIVE_REFERENCED_POLICY']
        if index == 2: markers.append('AGENTS_UPDATED_CORE_MARKER')
        return instruction_phase(markers, ('source', 'relocated', 'updated')[index])

    def test_valid(self):
        for index in range(3): check_canonical_phase(self.phase(index), 'link-distinct')

    def test_old_core_is_not_update_evidence(self):
        phase = self.phase(1)
        phase['label'] = 'updated'
        with self.assertRaises(AssertionError): check_canonical_phase(phase, 'link-distinct')

    def test_reference_base_change(self):
        phase = self.phase()
        for message in phase['requests'][0]['messages']:
            if message['role'] == 'system' and isinstance(message.get('content'), str):
                message['content'] = message['content'].replace('AGENTS_REFERENCED_POLICY', 'AGENTS_CANONICAL_REFERENCE_DECOY')
        with self.assertRaises(AssertionError): check_canonical_phase(phase, 'link-distinct')

    def test_missing_native_body(self):
        phase = self.phase()
        for message in phase['requests'][0]['messages']:
            if message['role'] == 'system' and isinstance(message.get('content'), str):
                message['content'] = message['content'].replace('AGENTS_OTHER_INSTRUCTION_MARKER', '')
        with self.assertRaises(AssertionError): check_canonical_phase(phase, 'link-distinct')


if __name__ == '__main__':
    unittest.main()
