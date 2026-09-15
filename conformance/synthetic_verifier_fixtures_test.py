"""Synthetic assertion inputs cannot satisfy current native evidence checks."""
import json
from pathlib import Path
import tempfile
import unittest

from evidence_state import assess_receipt
from synthetic_verifier_fixtures import (
    keymap_record, parent_skills_record, settings_record, snapshot, trust_record,
    skill_metadata_record,
)
from verify_copilot_skill_metadata import evaluate


class SyntheticFixtureTests(unittest.TestCase):
    def test_inputs_have_no_current_implementation_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = snapshot(temporary)
            for record in (keymap_record(path), parent_skills_record(path),
                           settings_record(path), trust_record(path, 'unattended')):
                with self.subTest(record=record.get('case', record.get('scope'))):
                    self.assertTrue(record['synthetic'])
                    self.assertFalse(record['full_adapter_support'])
                    path.write_text(json.dumps(record))
                    state = assess_receipt(path, Path(temporary), current_runner=path.with_suffix('.runner.py'))
                    self.assertTrue(state.integrity_valid)
                    self.assertFalse(state.current_eligible)
                    self.assertIn('implementation_sha256 is missing', state.eligibility_errors)

    def test_malformed_skill_fixture_has_a_valid_refusal(self):
        self.assertTrue(evaluate(skill_metadata_record(malformed=True))['discovery_verified'])

    def test_fixture_mutation_does_not_change_next_input(self):
        first = skill_metadata_record()
        first['target_hashes'].clear()
        self.assertTrue(evaluate(skill_metadata_record())['discovery_verified'])


if __name__ == '__main__':
    unittest.main()
