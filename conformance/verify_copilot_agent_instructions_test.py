#!/usr/bin/env python3
"""Reject instruction evidence with missing effects or changed scope."""
import unittest

from synthetic_instruction_fixtures import instruction_phase

from verify_copilot_agent_instructions import check_phase


class EvidenceTests(unittest.TestCase):
    def result(self, label='combined-root'):
        definitions = {'.claude/CLAUDE.md': 'SYNTHETIC_DOT_BODY'}
        references = {'.claude/policy.md': 'SYNTHETIC_DOT_POLICY'}
        if label == 'combined-root':
            definitions['AGENTS.md'] = 'SYNTHETIC_ROOT_BODY'
            references['child-policy.md'] = 'AGENTS_CHILD_REFERENCED_POLICY'
            markers = ['SYNTHETIC_ROOT_BODY', 'AGENTS_CHILD_REFERENCED_POLICY']
        elif label == 'dot-root':
            markers = []
        else:
            raise ValueError(label)
        phases = [instruction_phase(markers, stage) for stage in ('source', 'relocated')]
        for phase in phases:
            phase['instruction_markers'] = {name: body in markers for name, body in definitions.items()}
            phase['reference_markers'] = {name: body in markers for name, body in references.items()}
        return {'case': 'combined' if label == 'combined-root' else 'dot-only',
                'cwd_subdir': '.', 'definitions': definitions, 'references': references, 'phases': phases}

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
