#!/usr/bin/env python3
"""Require native effects and unchanged shared package identity for import claims."""
import json
import unittest

from verify_copilot_skill_import import BASE, package_check, phase_check


class EvidenceTests(unittest.TestCase):
    def record(self):
        return json.loads((BASE/'copilot-project-skill-agents-user-instructions.json').read_text())

    def test_valid(self):
        r = self.record()
        package_check(r)
        for phase in r['phases']: phase_check(phase)

    def test_missing_native_effect(self):
        phase = self.record()['phases'][1]
        phase['effect'] = None
        with self.assertRaises(AssertionError): phase_check(phase)

    def test_unapproved_execution(self):
        phase = self.record()['phases'][1]
        phase['approvals'] = []
        with self.assertRaises(AssertionError): phase_check(phase)

    def test_replaced_shared_file(self):
        r = self.record()
        r['source_inodes_preserved'] = False
        with self.assertRaises(AssertionError): package_check(r)

    def test_changed_shared_mode(self):
        r = self.record()
        r['projected_modes']['scripts/probe.py'] = 0o600
        with self.assertRaises(AssertionError): package_check(r)


if __name__ == '__main__':
    unittest.main()
