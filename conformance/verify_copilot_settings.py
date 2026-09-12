#!/usr/bin/env python3
"""Verify the bounded settings-file lifecycle without upgrading all fields."""
import json
from pathlib import Path
import re

from evidence_state import assess_receipt
from run_native_approvals import PINS, sha
from run_native_copilot_preferences import plain_terminal

ROOT = Path(__file__).resolve().parents[2]
RECEIPT = ROOT/'WORKBENCH/evidence/native-draft2-debug/copilot-settings-artifact.json'


def verify_record(record):
    assert record['passed'] and not record['full_adapter_support']
    assert record['native_version'] == '1.0.83' and record['native_sha256'] == PINS['copilot']
    assert record['runner_sha256'] == sha(RECEIPT.with_suffix('.runner.py'))
    assert [p['name'] for p in record['phases']] == ['plan', 'interactive', 'removed']
    sessions = set()
    for phase in record['phases']:
        name = phase['name']
        assert phase['preferences'] == phase['projected_preferences'] == phase['reimported_preferences']
        assert phase['settings_mode'] == 0o600 and phase['state_unchanged_by_apply'] and phase['settings_unchanged_by_native']
        assert phase['alive_before_teardown'] and not phase['alive_after_teardown']
        output = plain_terminal(phase['raw_output'])
        assert output == phase['output']
        if name == 'removed':
            assert 'statusLine' not in phase['projected_preferences']
            assert phase['status_events'] == [] and 'ODA_STATUS_' not in output
            assert '[Current]' not in output and 'tab next tab' not in output
            continue
        events = phase['status_events']
        assert len(events) >= 3
        ids = {e['value']['session_id'] for e in events}
        assert len(ids) == 1 and not ids & sessions
        sessions.update(ids)
        assert all(e['phase'] == name and e['value']['cwd'] == record['fixture']+'/workspace'
                   and e['value']['version'] == '1.0.83' and e['value']['model']['id'] == 'fixture-model' for e in events)
        interval = phase['preferences']['statusLine']['refreshInterval']
        gaps = [b['recorded_at']-a['recorded_at'] for a, b in zip(events, events[1:])]
        assert gaps == phase['refresh_gaps']
        assert sum(interval*.65 <= gap <= interval*1.5 for gap in gaps) >= 2
        assert 'ODA_STATUS_'+name in output
        if name == 'plan':
            assert ' · plan · ' in output and 'Gists' not in output
            assert re.search(r'Sessions\s+\[Current\]\s+Issues\s+Pull requests', output)
        else:
            assert ' · plan · ' not in output
            assert re.search(r'\[Current\]\s+Sessions\s+Issues\s+Pull requests\s+Gists', output)
    commands = record['commands']
    assert all(row['exit_code'] == 0 for row in commands)
    adapter = [row['command'] for row in commands if row['command'][0].endswith('/agents')]
    assert [cmd[1] for cmd in adapter] == ['apply', 'import']*3
    for cmd in adapter:
        assert cmd[cmd.index('--scope')+1] == 'user'
        assert cmd[cmd.index('--native-home')+1] == record['fixture']+'/home'


if __name__ == '__main__':
    record=json.loads(RECEIPT.read_text());verify_record(record)
    state=assess_receipt(RECEIPT,ROOT,current_runner=ROOT/'WORKBENCH/conformance/run_native_copilot_preferences.py',
                         current_helpers={'run_native_approvals.py':ROOT/'WORKBENCH/conformance/run_native_approvals.py'})
    assert state.integrity_valid,state.integrity_errors
    print('PASS: three settings sessions; field-specific limits and support gates retained; current eligibility: '+json.dumps({'current_eligible':state.current_eligible,'reasons':list(state.eligibility_errors)},sort_keys=True))
