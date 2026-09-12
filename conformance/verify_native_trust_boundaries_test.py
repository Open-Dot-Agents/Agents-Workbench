#!/usr/bin/env python3
import json
import unittest
from verify_native_trust_boundaries import BASE,FILES,verify_record

class EvidenceTests(unittest.TestCase):
    def record(self,case):
        path=BASE/FILES[case];return path,json.loads(path.read_text())
    def test_valid_unattended(self):
        path,record=self.record('unattended');verify_record(record,path)
    def test_valid_composed(self):
        path,record=self.record('composed-deny');verify_record(record,path)
    def test_valid_pending(self):
        path,record=self.record('pending-timeout');verify_record(record,path)
    def test_valid_disconnect(self):
        path,record=self.record('disconnect');verify_record(record,path)
    def test_valid_post_start_cleanup(self):
        path,record=self.record('post-start-disconnect');verify_record(record,path)
    def test_missing_effect_breaks_counterexample(self):
        path,record=self.record('unattended');record['first_effect']=None
        with self.assertRaises(AssertionError):verify_record(record,path)
    def test_wrong_effect_correlation(self):
        path,record=self.record('unattended');record['first_effect']='0'
        with self.assertRaises(AssertionError):verify_record(record,path)
    def test_composed_descendant_effect_refused(self):
        path,record=self.record('background-deny');record['second_effect']='SECOND'
        with self.assertRaises(AssertionError):verify_record(record,path)
    def test_uncorrelated_approval_refused(self):
        path,record=self.record('composed-deny');record['approvals'][0]['params']['itemId']='other'
        with self.assertRaises(AssertionError):verify_record(record,path)
    def test_project_sentinel_cannot_be_effective(self):
        path,record=self.record('unattended');record['requests'][0]['model']='ODA_DISABLED_PROJECT_MODEL'
        with self.assertRaises(AssertionError):verify_record(record,path)
    def test_pending_effect_refused(self):
        path,record=self.record('pending-timeout');record['first_effect_after_window']='started'
        with self.assertRaises(AssertionError):verify_record(record,path)
    def test_disconnect_response_refused(self):
        path,record=self.record('disconnect');record['approvals'][0]['response']={'decision':'decline'}
        with self.assertRaises(AssertionError):verify_record(record,path)
    def test_post_start_descendant_effect_refused(self):
        path,record=self.record('post-start-disconnect');record['second_effect']='SECOND'
        with self.assertRaises(AssertionError):verify_record(record,path)
    def test_post_start_completion_required(self):
        path,record=self.record('post-start-disconnect');record['command_completed_before_disconnect']=False
        with self.assertRaises(AssertionError):verify_record(record,path)

if __name__=='__main__':unittest.main()
