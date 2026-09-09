"""Ensure partial native coverage cannot produce a complete audit."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'conformance'))
import summarize_extended as summary


class ExtendedAuditTests(unittest.TestCase):
    def record(self, directory):
        content=b'test source';digest=hashlib.sha256(content).hexdigest()
        source=directory/'sources'/(digest+'-test.py');source.parent.mkdir();source.write_bytes(content)
        checks=[{'id':name,'passed':True} for name in summary.required_checks('codex','remote-https')]
        data={'vendor':'codex','case':'remote-https','package':summary.run_extended.native.VERSIONS['harnesses']['codex'],
              'passed':True,'outcome':'native-pass','checks':checks,'metadata':{'agentsSha256':'test'},
              'sources':{'test.py':digest},'transcripts':[{'kind':'native'}]}
        path=directory/'codex/remote-https.json';path.parent.mkdir();path.write_text(json.dumps(data))
        return path,data

    def test_missing_assertion_is_incomplete(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(summary.run_extended.CASES,{'remote-https':None},clear=True):
            directory=Path(temporary);path,data=self.record(directory)
            self.assertTrue(summary.audit(directory,['codex'])['coverageComplete'])
            data['checks']=[c for c in data['checks'] if c['id']!='native-mcp-call'];path.write_text(json.dumps(data))
            self.assertFalse(summary.audit(directory,['codex'])['coverageComplete'])

    def test_complete_failure_is_not_functional_success(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(summary.run_extended.CASES,{'remote-https':None},clear=True):
            directory=Path(temporary);path,data=self.record(directory)
            data['checks'][0]['passed']=False;data['passed']=False;data['outcome']='native-failure';path.write_text(json.dumps(data))
            result=summary.audit(directory,['codex'])
            self.assertTrue(result['coverageComplete'])
            self.assertFalse(result['functionalPass'])

    def test_missing_native_phase_is_incomplete(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(summary.run_extended.CASES,{'remote-https':None},clear=True):
            directory=Path(temporary);path,data=self.record(directory)
            data['transcripts']=[];path.write_text(json.dumps(data))
            self.assertFalse(summary.audit(directory,['codex'])['coverageComplete'])
