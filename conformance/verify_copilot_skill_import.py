#!/usr/bin/env python3
"""Verify project skill import without confusing copied files with execution."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'WORKBENCH/evidence/native-draft2-debug'
PIN = 'a3262c4513ef1fc2ca21485261ca73196977ad76bd5e7990fb572f6134aaeedd'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def phase_check(phase, present=True):
    assert phase['prompt']['stopReason'] == 'end_turn'
    assert phase['requests'] and phase['nonce'] in json.dumps(phase['requests'])
    assert phase['body_loaded'] is present
    assert any('AGENTS_SKILL_IMPORT_BODY' in json.dumps(r['messages']) for r in phase['requests']) is present
    rows = [s for s in phase['discovery'] if s['name'] == 'fixture-import']
    assert len(rows) == int(present)
    events = phase['native_events']
    assert any(e['type'] == 'session.start' and e['data']['sessionId'] == phase['session']['sessionId']
               and e['data']['copilotVersion'] == '1.0.83' for e in events)
    assert any(e['type'] == 'user.message' and phase['nonce'] in e['data']['content'] for e in events)
    assert any(e['type'] == 'assistant.turn_end' for e in events)
    invoked = [e['data'] for e in events if e['type'] == 'skill.invoked']
    assert len(invoked) == int(present)
    if not present:
        assert not phase['approvals'] and phase['effect'] is None
        assert any(e['type'] == 'tool.execution_complete' and e['data']['success'] is False
                   and e['data']['error']['message'] == 'Skill not found: fixture-import' for e in events)
        return
    assert rows[0]['path'] == phase['package'] and rows[0]['enabled'] and rows[0]['source'] == 'project'
    assert invoked[0]['path'] == phase['package']+'/SKILL.md' and invoked[0]['trigger'] == 'agent-invoked'
    assert 'AGENTS_SKILL_IMPORT_BODY' in invoked[0]['content']
    shell = [e['data'] for e in events if e['type'] == 'tool.execution_start' and e['data']['toolName'] == 'bash']
    assert len(shell) == 1
    command = '/usr/bin/python3 '+phase['package']+'/scripts/probe.py'
    assert shell[0]['arguments']['command'] == command
    approvals = phase['approvals']
    assert len(approvals) == 1 and approvals[0]['approved']
    assert approvals[0]['params']['sessionId'] == phase['session']['sessionId']
    assert approvals[0]['params']['toolCall']['toolCallId'] == shell[0]['toolCallId']
    assert approvals[0]['params']['toolCall']['rawInput']['command'] == command
    assert approvals[0]['response']['outcome']['optionId'] == 'allow_once'
    assert any(e['type'] == 'tool.execution_complete' and e['data']['toolCallId'] == shell[0]['toolCallId']
               and e['data']['success'] is True for e in events)
    assert phase['effect'] == 'AGENTS_SKILL_IMPORT_ASSET'


def package_check(r):
    assert r['source_unchanged'] and r['external_state_unchanged']
    assert r['source_modes_preserved'] and r['source_inodes_preserved']
    assert r['source_hashes'] == r['imported_hashes'] == r['projected_hashes'] == r['reimported_hashes']
    assert set(r['source_hashes']) == {'SKILL.md', 'data.txt', 'blob.bin', 'scripts/probe.py'}
    expected = r['source_modes'] if r['origin'] == 'agents' else {
        name: 0o700 if name == 'scripts/probe.py' else 0o600 for name in r['source_hashes']}
    assert r['projected_modes'] == expected


def verify(evidence_suffix='user-instructions'):
    files = {str(p.relative_to(ROOT)): sha(p) for p in (ROOT / 'CLI/internal/config').glob('*.go')}
    sources = json.loads((BASE/'copilot-shared-skills.sources.json').read_text())['sources']
    for source in sources: assert hashlib.sha256(source['body'].encode()).hexdigest() == source['sha256']
    assert '.agents/skills/' in sources[1]['body']
    for origin in ('github', 'claude', 'agents'):
        path = BASE / f'copilot-project-skill-{origin}-{evidence_suffix}.json'
        r = json.loads(path.read_text())
        assert r['passed'] and r['origin'] == origin and r['scope'] == 'project'
        assert r['native_sha256'] == PIN and r['native_version'] == '1.0.83'
        assert r['runner_sha256'] == sha(path.with_suffix('.runner.py')) == sha(ROOT / 'WORKBENCH/conformance/run_native_copilot_skill_import.py')
        assert r['helper_sha256'] == sha(ROOT / 'WORKBENCH/conformance/run_native_approvals.py')
        assert r['implementation_sha256'] == files and not r['full_adapter_support']
        package_check(r)
        assert all(c['exit_code'] == 0 for c in r['commands'])
        assert r['import_report']['imported_project_skills'] == {f'.{origin}/skills/fixture-import': 'skills/fixture-import'}
        assert r['plan']['applicable'] and not r['plan'].get('warnings')
        assert [p['label'] for p in r['phases']] == ['source', 'relocated']
        assert r['phases'][0]['package'] == r['fixture']+f'/source/.{origin}/skills/fixture-import'
        assert r['phases'][1]['package'] == r['fixture']+'/target/.agents/skills/fixture-import'
        for phase in r['phases']: phase_check(phase)

        old_path = BASE / f'copilot-project-skill-{origin}-before.json'
        before = json.loads(old_path.read_text())
        assert not before['passed']
        assert before['runner_sha256'] == sha(old_path.with_suffix('.runner.py'))
        assert before['native_sha256'] == PIN and before['implementation_sha256'] != files
        phase_check(before['phases'][0])
        if origin == 'agents':
            assert len(before['phases']) == 1 and before['commands'][-1]['exit_code'] != 0
            assert '.agents/manifest.json: file does not exist' in before['commands'][-1]['stderr']
        else:
            assert 'project skill assets were lost during native import' in before['error']
            assert before['imported_hashes'] == before['projected_hashes'] == {}
            phase_check(before['phases'][1], False)
    path = BASE/'copilot-shared-skill-import-before.json'
    before = json.loads(path.read_text())
    assert before['exit_code'] != 0 and before['test_sha256'] == sha(path.with_suffix('.test.go'))
    assert '.agents/manifest.json: file does not exist' in before['stdout'] and 'no recognized native configuration artifacts' in before['stdout']
    path = BASE / 'copilot-project-skill-import-before.json'
    before = json.loads(path.read_text())
    assert before['exit_code'] != 0 and before['stdout'].count('no recognized native configuration artifacts') == 2
    assert before['test_sha256'] == sha(path.with_suffix('.test.go'))
    path = BASE / 'copilot-project-skill-cli-user-instructions.json'
    public = json.loads(path.read_text())
    assert public['passed'] and not public['native_harness_execution']
    assert public['runner_sha256'] == sha(path.with_suffix('.runner.py')) == sha(ROOT / 'CLI/scripts/check_project_skill_import.py')
    for name, digest in public['source_sha256'].items(): assert sha(ROOT / name) == digest
    assert [case['origin'] for case in public['cases']] == ['github', 'claude']
    for case in public['cases']:
        assert case['before_refusal'] == case['after_refusal'] and case['before_refusal']
        assert case['backup_mode'] == 0o600 and case['backup_bytes'] == 0
        assert all(case[k] for k in ('validated_after_backup', 'backup_private', 'source_unchanged', 'overlap_refused', 'refusal_unchanged'))
    for command in public['commands']:
        if command['command'][1] == 'apply':
            assert command['exit_code'] != 0
            plan = json.loads(command['stdout'])
            assert not plan['applicable'] and plan['actions'] == []
            assert any('project skill discovery conflict' in message for message in plan['diagnostics'])
        else: assert command['exit_code'] == 0
    before = json.loads((BASE / 'copilot-project-skill-marker-backup-before.json').read_text())
    assert before['commands'][0]['exit_code'] == 0 and before['commands'][1]['exit_code'] != 0
    assert '.gitkeep.bak' in before['commands'][1]['stdout']
    return {'passed': True, 'native_cases': 3, 'native_processes': 6, 'completed_native_turns': 6,
            'scope': 'project', 'full_adapter_support': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-suffix', default='user-instructions')
    print(json.dumps(verify(parser.parse_args().evidence_suffix), indent=2))
