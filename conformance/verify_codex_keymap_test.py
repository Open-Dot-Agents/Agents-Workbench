#!/usr/bin/env python3
import tempfile
import unittest

from verify_codex_keymap import verify_record
from synthetic_verifier_fixtures import snapshot, keymap_record


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = snapshot(directory.name)

    def record(self):return keymap_record(self.path)
    def test_valid(self):verify_record(self.record(),self.path)
    def test_old_key_still_active(self):
        record=self.record();record['phases'][2]['keys'][1]['effect']=record['phases'][1]['keys'][1]['effect']
        with self.assertRaises(AssertionError):verify_record(record,self.path)
    def test_stale_effect(self):
        record=self.record();record['phases'][1]['keys'][1]['effect']['time']=0
        with self.assertRaises(AssertionError):verify_record(record,self.path)
    def test_unrelated_editor_process(self):
        record=self.record();record['phases'][1]['keys'][1]['effect']['parent']=-1
        with self.assertRaises(AssertionError):verify_record(record,self.path)
    def test_unbinding_lost_on_reimport(self):
        record=self.record();record['phases'][3]['reimported']={}
        with self.assertRaises(AssertionError):verify_record(record,self.path)
    def test_terminal_output_not_observed(self):
        record=self.record();record['phases'][1]['raw_output']='';record['phases'][1]['output']=''
        with self.assertRaises(AssertionError):verify_record(record,self.path)


if __name__=='__main__':unittest.main()
