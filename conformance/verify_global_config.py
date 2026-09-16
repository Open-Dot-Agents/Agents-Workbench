#!/usr/bin/env python3
"""Verify bounded global defaults and project overrides with native evidence."""
import json
from pathlib import Path

from evidence_state import assess_receipt
from run_native_approvals import PINS, sha

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT/'WORKBENCH/evidence/native-draft2-debug'
RECEIPTS = {'codex': 'global-config-codex-final.json', 'copilot': 'global-config-copilot-streaming-final.json'}


def verify_record(record, path):
    vendor = record['vendor']
    assert record['passed'] and not record['full_adapter_support'] and not record['provider_errors']
    assert record['native_sha256'] == PINS[vendor]
    assert record['native_version'] == ('0.154.0' if vendor == 'codex' else '1.0.84-9')
    assert record['runner_sha256'] == sha(path.with_suffix('.runner.py'))
    assert record['global_plan']['applicable'] and record['global_plan']['native']['scope'] == 'user'
    assert record['project_plan']['applicable'] and record['project_plan']['native']['scope'] == 'project'
    assert record['project_plan']['native']['global_source'] == record['fixture']+'/home/.agents'
    assert [p['label'] for p in record['phases']] == ['global', 'project', 'fallback']
    sessions = set()
    for phase in record['phases']:
        assert phase['session_id'] not in sessions and not phase['approvals']
        sessions.add(phase['session_id'])
        requests = phase['model_requests']
        expected = ('project-model' if phase['label'] == 'project' else 'global-model') if vendor == 'codex' else 'gpt-5.4'
        assert requests and all(r['model'] == expected and phase['nonce'] in json.dumps(r) for r in requests)
        assert 'AGENTS_GLOBAL_CORE' in json.dumps(requests)
        assert ('AGENTS_PROJECT_CORE' in json.dumps(requests)) == (phase['label'] != 'global')
        events = phase['events']
        assert any('AGENTS_GLOBAL_NATIVE_DONE' in json.dumps(e) and phase['session_id'] in json.dumps(e) for e in events)
        if vendor == 'codex':
            assert phase['effective_config']['config']['model'] == expected
            assert phase['completion']['params']['turn']['status'] == 'completed'
            assert phase['completion']['params']['threadId'] == phase['session_id']
            skills = [s for row in phase['discovery']['data'] for s in row['skills'] if s['name'] == 'global-shared-fixture']
        else:
            assert phase['completion']['stopReason'] == 'end_turn'
            skills = [s for s in phase['discovery'] if s['name'] == 'global-shared-fixture']
            assert skills and skills[0]['source'] == 'personal-agents' and skills[0]['enabled']
        assert len(skills) == 1
        expected_path = record['fixture']+'/home/.agents/skills/global-shared-fixture'
        assert skills[0]['path'] == expected_path+('/SKILL.md' if vendor == 'codex' else '')
    for name, before in record['user_before_project'].items():
        if name != 'config.json': assert record['user_after_project'][name] == before
    for row in record['commands']:
        assert row['exit_code'] == 0
        if row['command'][0].endswith('/agents'):
            assert row['authority_before'] == row['authority_after']
    global_writes = [r['command'] for r in record['commands'] if '--global' in r['command'] and r['command'][1] == 'apply']
    assert len(global_writes) == 1 and '--native-home' in global_writes[0]


def verify():
    eligibility = {}
    for vendor, name in RECEIPTS.items():
        path = BASE/name
        record = json.loads(path.read_text())
        assert record['vendor'] == vendor
        verify_record(record, path)
        state = assess_receipt(path, ROOT,
                               current_runner=ROOT/'WORKBENCH/conformance/run_native_global_config.py',
                               current_helper=ROOT/'WORKBENCH/conformance/run_native_approvals.py')
        assert state.integrity_valid, state.integrity_errors
        eligibility[vendor] = {'current_eligible': state.current_eligible,
                               'reasons': list(state.eligibility_errors)}
    print('PASS: six native global configuration sessions; historical integrity checked; current eligibility: '
          + json.dumps(eligibility, sort_keys=True))


if __name__ == '__main__': verify()
