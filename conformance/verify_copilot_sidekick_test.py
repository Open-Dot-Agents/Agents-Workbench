#!/usr/bin/env python3
import tempfile
from pathlib import Path
import unittest

from verify_copilot_sidekick import PINS, sha, verify_record


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='sidekick-verifier-test-')
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)/'synthetic.json'
        self.path.with_suffix('.runner.py').write_text('# Synthetic test snapshot; not native evidence.\n')

    def record(self):
        return {
            'passed': True, 'outcome': 'native-ignored', 'full_adapter_support': False,
            'adapter_mapping': False, 'native_version': '1.0.83', 'native_sha256': PINS['copilot'],
            'runner_sha256': sha(self.path.with_suffix('.runner.py')),
            'experimental': True, 'command': ['synthetic-copilot', '--experimental', '--acp'],
            'prompt': {'stopReason': 'end_turn'}, 'effect': '', 'approvals': [], 'provider_errors': [],
            'model_requests': [{'tools': [{'function': {'name': 'task', 'parameters': {
                'properties': {'agent_type': {'enum': ['agents-sidekick-fixture']}}}}}]}],
            'scope': 'user', 'native_logs': {'synthetic': 'agents/fixture.agent.md: unknown field ignored: sidekick'},
            'configuration': 'maxSendsPerTurn: 1\nevent: user.message\nbehavior: restart',
        }

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
