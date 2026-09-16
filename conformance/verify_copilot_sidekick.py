#!/usr/bin/env python3
"""Verify a pinned native parser limitation, not a successful mapping."""
import json
from pathlib import Path

from evidence_state import assess_receipt
from run_native_approvals import PINS, sha

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT/'WORKBENCH/evidence/native-draft2-debug'


def verify_record(record, path):
    assert record['passed'] and record['outcome'] == 'native-ignored'
    assert not record['full_adapter_support'] and not record['adapter_mapping']
    assert record['native_version'] == '1.0.84-9' and record['native_sha256'] == PINS['copilot']
    assert record['runner_sha256'] == sha(path.with_suffix('.runner.py'))
    assert record['experimental'] and '--experimental' in record['command'] and '--acp' in record['command']
    assert record['prompt']['stopReason'] == 'end_turn'
    assert record['effect'] == '' and record['approvals'] == [] and record['provider_errors'] == []
    assert len(record['model_requests']) == 1
    task = next(t for t in record['model_requests'][0]['tools'] if t['function']['name'] == 'task')
    assert 'agents-sidekick-fixture' in task['function']['parameters']['properties']['agent_type']['enum']
    source = '.github/agents/fixture.agent.md' if record['scope'] == 'project' else 'agents/fixture.agent.md'
    assert any(source+': unknown field ignored: sidekick' in value for value in record['native_logs'].values())
    assert 'maxSendsPerTurn: 1' in record['configuration'] and 'event: user.message' in record['configuration']
    assert 'behavior: restart' in record['configuration']


def verify():
    sessions = set()
    eligibility = {}
    for scope in ('project', 'user'):
        path = BASE/f'copilot-sidekick-{scope}-ignored.json'
        record = json.loads(path.read_text())
        assert record['scope'] == scope
        verify_record(record, path)
        state = assess_receipt(path, ROOT,
                               current_runner=ROOT/'WORKBENCH/conformance/run_native_copilot_sidekick.py',
                               current_helper=ROOT/'WORKBENCH/conformance/run_native_approvals.py')
        assert state.integrity_valid, state.integrity_errors
        eligibility[scope] = {'current_eligible': state.current_eligible,
                              'reasons': list(state.eligibility_errors)}
        identity = record['session']['sessionId']
        assert identity not in sessions
        sessions.add(identity)
    print('PASS: sidekick ignored by pinned Copilot in two isolated ACP sessions; no mapping enabled; '
          'current eligibility: ' + json.dumps(eligibility, sort_keys=True))


if __name__ == '__main__':
    verify()
