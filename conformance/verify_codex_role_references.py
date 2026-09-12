#!/usr/bin/env python3
"""Verify agent references and skill selectors after configuration relocation."""
import argparse
import hashlib
import json
from pathlib import Path

from evidence_state import assess_receipt

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'WORKBENCH/evidence/native-draft2-debug'
PIN = '3188814c35471432d4123203e0eb38e5bddc60226e3d7ddf0e59e649ea140022'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def phase_check(phase, skill=False):
    assert phase['turn']['status'] == 'completed'
    assert any(e.get('method') == 'turn/completed' and e['params']['threadId'] == phase['parent_id']
               and e['params']['turn']['id'] == phase['turn']['id'] for e in phase['events'])
    children = [r for r in phase['requests'] if 'AGENTS_REFERENCED_ROLE_INSTRUCTIONS' in json.dumps(r['body'].get('input', []))]
    assert children and all(r['body']['model'] == 'child-model' and r['body']['reasoning']['effort'] == 'low'
                            and phase['nonce'] in json.dumps(r['body']) for r in children)
    completed = [e['params'] for e in phase['events'] if e.get('method') == 'item/completed']
    spawned = {tid for p in completed if p.get('item', {}).get('tool') == 'spawnAgent'
               and p['item'].get('status') == 'completed' for tid in p['item'].get('receiverThreadIds', [])}
    assert spawned and any(e.get('method') == 'turn/completed' and e['params'].get('threadId') in spawned
                           and e['params']['turn']['status'] == 'completed' for e in phase['events'])
    if skill:
        parents = [r for r in phase['requests'] if r not in children]
        assert parents and all('AGENTS_REFERENCED_SKILL_DESCRIPTION' in json.dumps(r['body'].get('input', [])) for r in parents)
        assert all('AGENTS_REFERENCED_SKILL_DESCRIPTION' not in json.dumps(r['body'].get('input', [])) for r in children)


def verify(evidence_suffix='project-skills-final'):
    files = {str(p.relative_to(ROOT)): sha(p) for p in (ROOT / 'CLI/internal/config').glob('*.go')}
    eligibility = []
    for scope in ('project', 'user'):
        for case in ('external', 'managed', 'absolute', 'declared-only', 'managed-skill'):
            path = BASE / f'codex-role-reference-{case}-{scope}-{evidence_suffix}.json'
            record = json.loads(path.read_text())
            assert record['passed'] and record['scope'] == scope
            assert record['native_version'] == '0.154.0' and record['native_sha256'] == PIN
            assert record['reference'] == ('managed' if case == 'managed-skill' else case)
            assert record['skill_reference'] is (case == 'managed-skill')
            assert record['runner_sha256'] == sha(path.with_suffix('.runner.py'))
            state = assess_receipt(path, ROOT, current_runner=ROOT/'WORKBENCH/conformance/run_native_codex_role_references.py',
                                   current_helper=ROOT/'WORKBENCH/conformance/run_native_approvals.py')
            assert state.integrity_valid, state.integrity_errors
            eligibility.append(state.current_eligible)
            assert record['source_unchanged'] and not record['full_adapter_support']
            assert all(c['exit_code'] == 0 for c in record['commands'])
            assert record['imported_config'] == record['projected_config'] == record['reimported_config']
            reference = record['projected_config']['agents']['fixture_reviewer']['config_file']
            if case in ('managed', 'managed-skill'): assert reference == 'agents/fixture.toml'
            else: assert Path(reference).is_absolute() and reference.startswith(record['fixture']+'/source/')
            assert [p['label'] for p in record['phases']] == ['source', 'relocated']
            for phase in record['phases']:
                assert phase['correlated']
                phase_check(phase, case == 'managed-skill')
            if case == 'managed-skill':
                selector = record['projected_role']['skills']['config'][0]
                assert selector['enabled'] is False and Path(selector['path']).is_absolute()
    for name, symptom in [('codex-role-reference-external-before', 'referenced role did not produce a child model request'),
                          ('codex-agent-skill-reference-user-before', 'role skill reference changed location')]:
        path = BASE / (name+'.json')
        before = json.loads(path.read_text())
        assert not before['passed'] and symptom in before['error']
        assert before['runner_sha256'] == sha(path.with_suffix('.runner.py')) and before['native_sha256'] == PIN
        assert before['implementation_sha256'] != files
        phase_check(before['phases'][0], 'skill' in name)
        second = before['phases'][1]
        assert second['turn']['status'] == 'completed'
        children = [r for r in second['requests'] if r['child']]
        if 'skill' in name:
            assert children and any('AGENTS_REFERENCED_SKILL_DESCRIPTION' in json.dumps(r['body'].get('input', [])) for r in children)
        else:
            assert not children and before['projected_config']['agents']['fixture_reviewer']['config_file'] == 'role-library/fixture.toml'
    for name in ('codex-role-reference-validation-before', 'codex-agent-skill-reference-validation-before'):
        path = BASE / (name+'.json')
        before = json.loads(path.read_text())
        assert before['exit_code'] != 0 and 'reference changed location' in before['stdout']
        assert before['test_sha256'] == sha(path.with_suffix('.test.go'))
    return {'passed': True, 'native_cases': 10, 'native_processes': 20, 'completed_native_turns': 40,
            'scope': 'project|user', 'historical_integrity': True,
            'current_support_eligible': all(eligibility), 'full_adapter_support': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-suffix', choices=['project-skills-final', 'project-skills', 'skill-rechecked', 'skill-current', 'verified', 'checked'], default='project-skills-final')
    print(json.dumps(verify(parser.parse_args().evidence_suffix), indent=2))
