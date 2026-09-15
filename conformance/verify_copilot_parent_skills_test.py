#!/usr/bin/env python3
import tempfile
from unittest import mock
import unittest

from verify_copilot_parent_skills import verify_record


from synthetic_verifier_fixtures import snapshot, parent_skills_record


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = snapshot(directory.name)
        patch = mock.patch('verify_copilot_parent_skills.RECEIPT', self.path)
        patch.start()
        self.addCleanup(patch.stop)

    def record(self):return parent_skills_record(self.path)
    def test_valid(self):verify_record(self.record())
    def test_child_capture_refused(self):
        record=self.record();record['child_did_not_capture_parent']=False
        with self.assertRaises(AssertionError):verify_record(record)
    def test_parent_asset_change_refused(self):
        record=self.record();record['parent_unchanged_by_child']=False
        with self.assertRaises(AssertionError):verify_record(record)
    def test_wrong_source_provenance(self):
        record=self.record()
        for item in record['phases'][1]['discovery']:
            if item['name']=='fixture-import':item['source']='project'
        with self.assertRaises(AssertionError):verify_record(record)
    def test_override_did_not_take_effect(self):
        record=self.record();record['phases'][3]['effect']='AGENTS_PARENT_UPDATED'
        with self.assertRaises(AssertionError):verify_record(record)
    def test_nested_repository_cannot_reuse_parent_effect(self):
        record=self.record();record['phases'][5]['effect']='AGENTS_PARENT_UPDATED'
        with self.assertRaises(AssertionError):verify_record(record)


if __name__=='__main__':unittest.main()
