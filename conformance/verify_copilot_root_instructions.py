#!/usr/bin/env python3
"""Verify root instruction loading, loss refusals, and native reference bases."""
import json
from pathlib import Path

from evidence_state import summarize_receipts

from run_native_approvals import PINS, sha

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT/'WORKBENCH/evidence/native-draft2-debug'


def check_phase(phase, case):
    assert phase['prompt']['stopReason'] == 'end_turn' and not phase['approvals']
    assert len(phase['requests']) == 2
    first = phase['requests'][0]['messages']
    context = '\n'.join(m['content'] for m in first if m['role'] == 'system' and isinstance(m.get('content'), str))
    assert ('AGENTS_ROOT_INSTRUCTION_MARKER' in context) is (phase['label'] != 'removed')
    assert ('AGENTS_OTHER_INSTRUCTION_MARKER' in context) is (case == 'distinct')
    if case == 'reference':
        assert ('AGENTS_REFERENCED_POLICY' in context) is (phase['label'] != 'unconverted')
        assert ('AGENTS_WRONG_REFERENCE_BASE' in context) is (phase['label'] == 'unconverted')
    if case.startswith('github-reference'):
        assert ('AGENTS_WRONG_REFERENCE_BASE' in context) is (phase['label'] != 'removed')
        assert 'AGENTS_REFERENCED_POLICY' not in context and 'AGENTS_WRONG_ROOT_POLICY' not in context
        assert ('AGENTS_SEPARATE_ROOT_MARKER' in context) is (case == 'github-reference-combined')
        assert ('AGENTS_SEPARATE_ROOT_POLICY' in context) is (case == 'github-reference-combined')
        assert ('AGENTS_UPDATED_GITHUB_MARKER' in context) is (phase['label'] == 'updated')
    assert phase['nonce'] in json.dumps(first)
    assert 'AGENTS_FILE_READ' in json.dumps(phase['requests'][1]['messages'])
    events = phase['native_events']
    session = phase['session']['sessionId']
    assert any(e['type'] == 'session.start' and e['data']['sessionId'] == session and e['data']['copilotVersion'] == '1.0.84-9'
               and e['data']['context']['cwd'] == phase['workspace'] for e in events)
    assert any(e['type'] == 'user.message' and phase['nonce'] in e['data']['content'] for e in events)
    reads = [e['data'] for e in events if e['type'] == 'tool.execution_start']
    assert len(reads) == 1 and reads[0]['toolName'] == 'view'
    assert reads[0]['arguments'] == {'path': str(Path(phase['workspace'])/'fixture.txt')}
    assert any(e['type'] == 'tool.execution_complete' and e['data']['toolCallId'] == reads[0]['toolCallId']
               and e['data']['success'] is True and 'AGENTS_FILE_READ' in e['data']['result']['content'] for e in events)
    assert any(e['type'] == 'assistant.turn_end' for e in events)
    assert any(e.get('method') == 'session/update' and e['params']['sessionId'] == session for e in phase['events'])


def verify():
    implementation = {str(p.relative_to(ROOT)): sha(p) for p in (ROOT/'CLI/internal/config').glob('*.go')}
    receipts = []
    for case in ('root', 'identical', 'distinct', 'reference'):
        suffix = 'github-reference-final' if case == 'identical' else 'github-reference-retry'
        path = BASE/f'copilot-root-instructions-{case}-{suffix}.json'
        receipts.append(path)
        result = json.loads(path.read_text())
        assert result['passed'] and not result['full_adapter_support'] and result['case'] == case
        assert result['native_version'] == '1.0.84-9' and result['native_sha256'] == PINS['copilot']
        assert result['runner_sha256'] == sha(path.with_suffix('.runner.py'))
        assert result['source_unchanged'] and result['authority_unchanged']
        expected = ['source', 'relocated']
        assert [p['label'] for p in result['phases']] == expected
        for phase in result['phases']: check_phase(phase, case)
        commands = result['commands']
        assert commands[0]['command'][0:2] == ['go', 'build'] and commands[0]['exit_code'] == 0
        assert result['roundtrip_preserved'] and result['plan']['applicable']
        assert [c['command'][1] for c in commands[1:]] == ['import', 'plan', 'apply', 'import']
        assert all(c['exit_code'] == 0 for c in commands)
    state = summarize_receipts(receipts, ROOT,
                               current_runner=ROOT/'WORKBENCH/conformance/run_native_copilot_root_instructions.py',
                               current_helper=ROOT/'WORKBENCH/conformance/run_native_approvals.py')
    assert state['historical_integrity'], state['integrity_errors']
    print(json.dumps({'passed': True, 'native_cases': 4, **state, 'full_adapter_support': False}, indent=2))


if __name__ == '__main__':
    verify()
