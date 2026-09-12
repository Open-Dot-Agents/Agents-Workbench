#!/usr/bin/env python3
"""Verify user-scope skill provenance, native execution, and file lifecycle."""
import json

from evidence_state import summarize_receipts
from verify_copilot_skill_import import ROOT, BASE, PIN, sha, phase_check


def verify():
    implementation = {str(p.relative_to(ROOT)): sha(p) for p in (ROOT/'CLI/internal/config').glob('*.go')}
    sessions = set()
    receipts = []
    for origin in ('copilot', 'agents'):
        path = BASE/f'copilot-user-skills-{origin}-verified.json'
        receipts.append(path)
        result = json.loads(path.read_text())
        assert result['passed'] and result['origin'] == origin and result['scope'] == 'user'
        assert result['native_version'] == '1.0.83' and result['native_sha256'] == PIN
        assert not result['full_adapter_support']
        assert result['runner_sha256'] == sha(path.with_suffix('.runner.py'))
        assert result['source_unchanged'] and result['source_modes_preserved'] and result['source_inodes_preserved']
        assert result['external_state_unchanged'] and result['lifecycle_external_state_unchanged'] and result['removed_assets_absent']
        assert result['source_hashes'] == result['imported_hashes'] == result['projected_hashes'] == result['reimported_hashes']
        assert set(result['source_hashes']) == {'SKILL.md', 'data.txt', 'blob.bin', 'scripts/probe.py'}
        assert result['projected_modes'] == {name: 0o700 if name == 'scripts/probe.py' else 0o600 for name in result['source_hashes']}
        assert [phase['label'] for phase in result['phases']] == ['source', 'relocated', 'updated', 'removed']
        for phase in result['phases']:
            source = 'personal-'+origin if phase['label'] == 'source' else 'personal-copilot'
            effect = 'AGENTS_USER_SKILL_UPDATED_ASSET' if phase['label'] == 'updated' else 'AGENTS_SKILL_IMPORT_ASSET'
            phase_check(phase, present=phase['label'] != 'removed', source=source, effect=effect)
            if phase['label'] != 'source':
                assert phase['workspace'] == result['fixture']+'/target-workspace'
                assert phase['package'] == result['fixture']+'/target-home/.copilot/skills/fixture-import'
            else:
                assert phase['package'] == result['fixture']+f'/home/.{origin}/skills/fixture-import'
            session = phase['session']['sessionId']
            assert session not in sessions
            sessions.add(session)
        assert result['plan']['applicable'] and result['plan']['native']['scope'] == 'user'
        adapter = [row for row in result['commands'] if row['command'][0].endswith('/agents')]
        assert [row['command'][1] for row in adapter] == ['import', 'plan', 'apply', 'import', 'apply', 'apply']
        for row in adapter:
            assert row['external_before'] == row['external_after'] and len(row['external_before']) == 3
            assert row['command'][row['command'].index('--scope')+1] == 'user'
            assert '--native-home' in row['command'] and row['exit_code'] == 0
        assert all(row['exit_code'] == 0 for row in result['commands'])
    assert len(sessions) == 8
    state=summarize_receipts(receipts,ROOT,current_runner=ROOT/'WORKBENCH/conformance/run_native_copilot_user_skills.py',
                             current_helper=ROOT/'WORKBENCH/conformance/run_native_approvals.py')
    assert state['historical_integrity'],state['integrity_errors']
    print(json.dumps({'passed':True,'native_cases':2,**state,'full_adapter_support':False},indent=2))


if __name__ == '__main__':
    verify()
