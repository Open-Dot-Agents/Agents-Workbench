#!/usr/bin/env python3
"""Reject project fallback and uncorrelated user skill execution evidence."""
import json
import unittest

from verify_copilot_skill_import import BASE, phase_check


class EvidenceTests(unittest.TestCase):
    def phase(self, index=1):
        return json.loads((BASE/'copilot-user-skills-copilot-verified.json').read_text())['phases'][index]

    def test_valid_user_phase(self):
        phase_check(self.phase(), source='personal-copilot')

    def test_project_fallback_refused(self):
        phase = self.phase()
        for row in phase['discovery']:
            if row['name'] == 'fixture-import': row['source'] = 'project'
        with self.assertRaises(AssertionError): phase_check(phase, source='personal-copilot')

    def test_wrong_asset_effect(self):
        phase = self.phase()
        phase['effect'] = 'stale effect'
        with self.assertRaises(AssertionError): phase_check(phase, source='personal-copilot')

    def test_wrong_skill_package(self):
        phase = self.phase()
        phase['package'] = '/unrelated/package'
        with self.assertRaises(AssertionError): phase_check(phase, source='personal-copilot')

    def test_missing_permission_correlation(self):
        phase = self.phase()
        phase['approvals'][0]['params']['toolCall']['toolCallId'] = 'unrelated'
        with self.assertRaises(AssertionError): phase_check(phase, source='personal-copilot')

    def test_removed_skill_cannot_reuse_loaded_phase(self):
        with self.assertRaises(AssertionError): phase_check(self.phase(), present=False, source='personal-copilot')


if __name__ == '__main__':
    unittest.main()
