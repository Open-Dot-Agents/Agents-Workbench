#!/usr/bin/env python3
"""Check that uncorrelated native events cannot prove instruction loading."""
import copy
import json
import unittest

from verify_copilot_recursive_instructions import check_phase, check_catalog
from synthetic_verifier_fixtures import recursive_phase, instruction_read_permission, instruction_edit_permission
from run_native_copilot_recursive_instructions import fixture_instruction_read
from run_native_copilot_instruction_triggers import fixture_edit_allowed


class RecursiveInstructionEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.phase = recursive_phase()

    def test_correlated_record(self):
        check_phase(self.phase, True, True)

    def test_verdict_without_body_cannot_pass(self):
        self.phase['requests'] = json.loads(json.dumps(self.phase['requests']).replace('AGENTS_NESTED_INSTRUCTION_BODY', 'MISSING_BODY'))
        with self.assertRaises(AssertionError): check_phase(self.phase, True, True)

    def test_other_file_cannot_pass(self):
        self.phase['file'] += '.other'
        with self.assertRaises(AssertionError): check_phase(self.phase, True, True)

    def test_missing_tool_completion_cannot_pass(self):
        self.phase['native_events'] = [e for e in self.phase['native_events'] if e['type'] != 'tool.execution_complete']
        with self.assertRaises(AssertionError): check_phase(self.phase, True, True)

    def test_other_session_cannot_pass(self):
        self.phase['session']['sessionId'] = 'unrelated'
        with self.assertRaises(AssertionError): check_phase(self.phase, True, True)

    def test_missing_file_result_cannot_pass(self):
        for event in self.phase['native_events']:
            if event['type'] == 'tool.execution_complete': event['data']['result']['content'] = 'unrelated'
        with self.assertRaises(AssertionError): check_phase(self.phase, True, True)

    def test_catalog_is_not_the_body(self):
        phase = recursive_phase(catalog=True)
        paths = ['.github/instructions/flat.instructions.md', '.github/instructions/nested/deep/fixture.instructions.md']
        check_catalog(phase, paths)
        check_phase(phase, True, True, paths)
        phase['requests'][0]['messages'][0]['content'] += '\nAGENTS_FLAT_INSTRUCTION_BODY'
        with self.assertRaises(AssertionError): check_catalog(phase, paths)

    def test_read_permission_is_bound_to_exact_fixture_files(self):
        params = instruction_read_permission()
        phase = {'session': {'sessionId': params['sessionId']}}
        paths = {params['toolCall']['rawInput']['path']}
        self.assertTrue(fixture_instruction_read(params, paths, phase['session']['sessionId']))
        for change in ('path', 'session', 'kind', 'extra'):
            altered = copy.deepcopy(params)
            if change == 'path': altered['toolCall']['rawInput']['path'] += '/outside'
            if change == 'session': altered['sessionId'] = 'other'
            if change == 'kind': altered['toolCall']['kind'] = 'edit'
            if change == 'extra': altered['toolCall']['rawInput']['other'] = True
            self.assertFalse(fixture_instruction_read(altered, paths, phase['session']['sessionId']))

    def test_edit_permission_rejects_additional_changes(self):
        record = instruction_edit_permission()
        params = record['approvals'][0]['params']
        self.assertTrue(fixture_edit_allowed(params, record['file'], record['session']['sessionId']))
        params['toolCall']['rawInput']['diff'] += '\n+Unapproved change\n'
        self.assertFalse(fixture_edit_allowed(params, record['file'], record['session']['sessionId']))


if __name__ == '__main__':
    unittest.main()
