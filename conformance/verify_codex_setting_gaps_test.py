#!/usr/bin/env python3
import tempfile
from pathlib import Path
import unittest

from verify_codex_setting_gaps import PINS, sha, verify_record


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='setting-verifier-test-')
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)/'synthetic.json'
        self.path.with_suffix('.runner.py').write_text('# Synthetic test snapshot; not native evidence.\n')

    def record(self):
        return {
            'native_version': '0.154.0', 'native_sha256': PINS['codex'],
            'runner_sha256': sha(self.path.with_suffix('.runner.py')),
            'full_adapter_support': False, 'adapter_mapping': False,
            'config_unchanged': True, 'provider_errors': [], 'approvals': [],
            'native_completed': True, 'case': 'mcp-remote',
            'thread': {'thread': {'id': 'synthetic-thread'}},
            'completion': {'params': {'threadId': 'synthetic-thread', 'turn': {'status': 'completed'}}},
            'model_requests': [{'input': 'AGENTS_SETTING_PROBE_REQUEST',
                                'reasoning': {'effort': 'medium', 'summary': 'auto'}}],
            'events': [{'threadId': 'synthetic-thread', 'output': 'AGENTS_SETTING_PROBE_DONE'},
                       {'method': 'mcpServer/startupStatus/updated', 'params': {
                           'name': 'fixture', 'status': 'ready', 'threadId': 'synthetic-thread'}}],
            'native_pid': 100, 'fixture': '/synthetic-fixture',
            'mcp_effect': {'parent': 100, 'pid': 101, 'cwd': '/synthetic-fixture/workspace'},
        }
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
