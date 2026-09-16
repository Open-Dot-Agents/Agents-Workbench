#!/usr/bin/env python3
import json
import unittest
import tempfile
from pathlib import Path

from verify_global_config import PINS, sha, verify_record


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='global-verifier-test-')
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)/'synthetic.json'
        self.path.with_suffix('.runner.py').write_text('# Synthetic unit-test snapshot; not native evidence.\n')

    def record(self, vendor='codex'):
        # Construct verifier inputs without captured sessions or local receipts.
        fixture = '/synthetic-fixture'
        phases = []
        for label in ('global', 'project', 'fallback'):
            session = 'synthetic-' + label
            model = ('project-model' if label == 'project' else 'global-model') if vendor == 'codex' else 'gpt-5.4'
            skill = {'name': 'global-shared-fixture',
                     'path': fixture+'/home/.agents/skills/global-shared-fixture'+('/SKILL.md' if vendor == 'codex' else ''),
                     'source': 'personal-agents', 'enabled': True}
            phases.append({
                'label': label, 'session_id': session, 'nonce': 'synthetic-nonce', 'approvals': [],
                'model_requests': [{'model': model, 'input': 'synthetic-nonce AGENTS_GLOBAL_CORE' +
                                    (' AGENTS_PROJECT_CORE' if label != 'global' else '')}],
                'events': [{'session': session, 'output': 'AGENTS_GLOBAL_NATIVE_DONE'}],
                'effective_config': {'config': {'model': model}},
                'completion': {'params': {'turn': {'status': 'completed'}, 'threadId': session}}
                              if vendor == 'codex' else {'stopReason': 'end_turn'},
                'discovery': {'data': [{'skills': [skill]}]} if vendor == 'codex' else [skill],
            })
        return {
            'vendor': vendor, 'fixture': fixture, 'passed': True, 'full_adapter_support': False,
            'provider_errors': [], 'native_sha256': PINS[vendor],
            'native_version': '0.154.0' if vendor == 'codex' else '1.0.84-9',
            'runner_sha256': sha(self.path.with_suffix('.runner.py')),
            'global_plan': {'applicable': True, 'native': {'scope': 'user'}},
            'project_plan': {'applicable': True, 'native': {'scope': 'project', 'global_source': fixture+'/home/.agents'}},
            'phases': phases, 'user_before_project': {'config.toml': 'unchanged'},
            'user_after_project': {'config.toml': 'unchanged'},
            'commands': [{'command': ['/synthetic/agents', 'apply', '--global', '--native-home', fixture+'/home'],
                          'exit_code': 0, 'authority_before': {'trust': 'unchanged'},
                          'authority_after': {'trust': 'unchanged'}}],
        }

    def test_valid_copilot(self): verify_record(self.record('copilot'), self.path)

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
