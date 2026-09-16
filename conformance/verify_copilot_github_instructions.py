#!/usr/bin/env python3
"""Verify GitHub instruction references before and after native projection."""
import json

from evidence_state import summarize_receipts
from verify_copilot_root_instructions import ROOT, BASE, check_phase
from run_native_approvals import PINS, sha


def verify():
    sessions = set()
    receipts = []
    for case, name in [('github-reference', 'link'), ('github-reference-regular', 'regular'),
                       ('github-reference-combined', 'combined')]:
        path = BASE/f'copilot-github-reference-{name}-source-final.json'
        receipts.append(path)
        result = json.loads(path.read_text())
        assert result['passed'] and result['case'] == case and not result['full_adapter_support']
        assert result['native_version'] == '1.0.84-9' and result['native_sha256'] == PINS['copilot']
        assert result['runner_sha256'] == sha(path.with_suffix('.runner.py'))
        assert result['source_unchanged'] and result['authority_unchanged'] and result['roundtrip_preserved']
        assert result['update_and_removal_preserved'] and result['plan']['applicable']
        assert [p['label'] for p in result['phases']] == ['source', 'relocated', 'updated', 'removed']
        for phase in result['phases']:
            check_phase(phase, case)
            session = phase['session']['sessionId']
            assert session not in sessions
            sessions.add(session)
        commands = result['commands']
        assert commands[0]['command'][:2] == ['go', 'build']
        assert [c['command'][1] for c in commands[1:]] == ['import', 'plan', 'apply', 'import', 'apply', 'import', 'apply']
        assert all(c['exit_code'] == 0 for c in commands)
    assert len(sessions) == 12
    state = summarize_receipts(receipts, ROOT,
                               current_runner=ROOT/'WORKBENCH/conformance/run_native_copilot_root_instructions.py',
                               current_helper=ROOT/'WORKBENCH/conformance/run_native_approvals.py')
    assert state['historical_integrity'], state['integrity_errors']
    print(json.dumps({'passed': True, 'native_cases': 3, **state, 'full_adapter_support': False}, indent=2))


if __name__ == '__main__':
    verify()
