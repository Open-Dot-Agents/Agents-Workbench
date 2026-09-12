#!/usr/bin/env python3
import json
import unittest

from verify_codex_setting_gaps import BASE,verify_record


class EvidenceTests(unittest.TestCase):
    path=BASE/'codex-setting-mcp-remote-final.json'
    def record(self):return json.loads(self.path.read_text())
    def test_valid(self):verify_record(self.record(),self.path,True)
    def test_missing_local_effect(self):
        record=self.record();record['mcp_effect']['cwd']='/other'
        with self.assertRaises(AssertionError):verify_record(record,self.path,True)
    def test_unrelated_child_process(self):
        record=self.record();record['mcp_effect']['parent']=-1
        with self.assertRaises(AssertionError):verify_record(record,self.path,True)
    def test_uncorrelated_ready_event(self):
        record=self.record()
        for event in record['events']:
            if event.get('method')=='mcpServer/startupStatus/updated':event['params']['threadId']='unrelated'
        with self.assertRaises(AssertionError):verify_record(record,self.path,True)
    def test_reasoning_flag_took_effect(self):
        record=self.record();record['model_requests'][0]['reasoning'].pop('summary')
        with self.assertRaises(AssertionError):verify_record(record,self.path,True)


if __name__=='__main__':unittest.main()
