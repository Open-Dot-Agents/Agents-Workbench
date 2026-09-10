"""Reject incomplete or mixed native evidence before release."""
import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'conformance'))
import release_gate
import run_extended
import summarize_extended
from evidence_validation_test import required_checks, required_metadata

spec = importlib.util.spec_from_file_location('compatibility_check', ROOT.parent/'CLI/scripts/check_compatibility.py')
compatibility = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compatibility)


class ReleaseGateTests(unittest.TestCase):
    def fixture(self, directory):
        metadata = required_metadata()
        metadata.update(agentsSha256='a'*64, runnerSha256=hashlib.sha256((ROOT/'conformance/run_adapter.py').read_bytes()).hexdigest(),
                        sourceCommits={c:'b'*40 for c in ('.','CLI','SPEC','WORKBENCH')},
                        sourceDirty={c:False for c in ('.','CLI','SPEC','WORKBENCH')})
        baseline = dict(schemaVersion='1.0.0', standardVersion='1.0.0', implementation='reference-cli-codex',
                        implementationVersion='1.0.0', **{'class':'adapter'}, passed=True,
                        checks=required_checks(), metadata=metadata)
        (directory/'codex.json').write_text(json.dumps(baseline))
        sources = directory/'extended/sources'; sources.mkdir(parents=True)
        for name, content in run_extended.SNAPSHOTS.items():
            (sources/(run_extended.SOURCES[name]+'-'+name)).write_bytes(content)
        target = directory/'extended/codex';target.mkdir()
        for case in run_extended.cases_for('codex'):
            checks=[dict(id=name,passed=True) for name in summarize_extended.required_checks('codex',case)]
            if case=='refusal-boundaries': checks += [dict(id='adapter-refuses-before-writes',passed=True)]*6
            phases={'instruction-precedence':2,'profile-tools':4,'profile-skills':2,'resumed-refresh':2,
                    'resumed-resources':2,'profile-hooks':3,'hook-exit-codes':2}.get(case,1)
            refusal=summarize_extended.is_refusal('codex',case)
            record=dict(vendor='codex',case=case,package=run_extended.native.VERSIONS['harnesses']['codex'],
                        passed=True,outcome='adapter-refusal' if refusal else 'native-pass',checks=checks,
                        metadata=metadata,sources=run_extended.SOURCES,preflight=[dict(id='test-preflight',passed=True)],
                        transcripts=[dict(kind='native')]*(0 if refusal else phases))
            (target/(case+'.json')).write_text(json.dumps(record))
        return baseline

    def test_complete_fixture_and_rejection_boundaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary);self.fixture(directory)
            self.assertEqual(release_gate.verify(directory,['codex']),[])
            for case in ['profile-skills','missing-env']:
                path=directory/'extended/codex'/(case+'.json');original=path.read_text()
                data=json.loads(original);data['checks'][0]['passed']=False;data['passed']=False
                data['outcome']='native-failure';path.write_text(json.dumps(data))
                self.assertTrue(release_gate.verify(directory,['codex']))
                path.write_text(original)
            path=directory/'extended/codex/remote-https.json';original=path.read_text()
            for change in ['version','binary','source','assertion','missing']:
                data=json.loads(original)
                if change=='version':data['package']['version']='0.0.0'
                if change=='binary':data['metadata']['agentsSha256']='c'*64
                if change=='source':data['metadata']['sourceCommits']['CLI']='c'*40
                if change=='assertion':data['checks']=[]
                if change=='missing':path.unlink()
                else:path.write_text(json.dumps(data))
                self.assertTrue(release_gate.verify(directory,['codex']),change)
                path.write_text(original)
            self.assertTrue(release_gate.verify(directory))

    def test_preflight_cannot_replace_native_baseline(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary);baseline=self.fixture(directory)
            baseline['metadata']['runMode']='preflight'
            (directory/'codex.json').write_text(json.dumps(baseline))
            self.assertTrue(release_gate.verify(directory,['codex']))

    def test_dirty_release_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary);baseline=self.fixture(directory)
            baseline['metadata']['sourceDirty']['CLI']=True
            (directory/'codex.json').write_text(json.dumps(baseline))
            self.assertTrue(any('uncommitted' in error for error in release_gate.verify(directory,['codex'],release=True)))

    def test_registry_cannot_promote_without_durable_evidence(self):
        data=json.loads((ROOT.parent/'CLI/compatibility.json').read_text())
        self.assertEqual(len(compatibility.check_release_support(data)),3)
        promoted=copy.deepcopy(data)
        for row in promoted['adapters']:
            row['status']='conformance-supported'
            row['capabilities']={key:'transformed' for key in row['capabilities']}
            row['evidence']='Pinned native results'
        self.assertTrue(compatibility.check_supported_evidence(promoted))
        self.assertEqual(compatibility.check_release_support(promoted),[])
        promoted['adapters'].pop()
        self.assertTrue(compatibility.check_release_support(promoted))
