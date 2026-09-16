#!/usr/bin/env python3
"""Verify native agent instruction bodies, reference bases, and working-directory scope."""
import json
from pathlib import Path

from evidence_state import summarize_receipts

from run_native_approvals import PINS, sha

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT/'WORKBENCH/evidence/native-draft2-debug'
CASES = [('combined', '.', 'combined-root'), ('combined', '.claude', 'combined-cwd'),
         ('dot-only', '.', 'dot-root'), ('dot-only', '.claude', 'dot-cwd')]


def check_phase(phase, result):
    assert phase['prompt']['stopReason'] == 'end_turn' and not phase['approvals']
    assert len(phase['requests']) == 2
    first = phase['requests'][0]['messages']
    context = '\n'.join(m['content'] for m in first if m['role'] == 'system' and isinstance(m.get('content'), str))
    dot = result['cwd_subdir'] == '.claude'
    for name, body in result['definitions'].items():
        expected = name != '.claude/CLAUDE.md' or dot
        assert (body.splitlines()[0] in context) is expected
        assert phase['instruction_markers'][name] is expected
    references = {'.claude/policy.md'} if dot else set()
    if result['case'] == 'combined': references.update({'root-policy.md', 'child-policy.md', 'claude-policy.md', '.github/policy.md'})
    for name, body in result['references'].items():
        assert (body.splitlines()[0] in context) is (name in references)
        assert phase['reference_markers'][name] is (name in references)
    assert phase['nonce'] in json.dumps(first)
    assert 'AGENTS_FILE_READ' in json.dumps(phase['requests'][1]['messages'])
    session = phase['session']['sessionId']
    events = phase['native_events']
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
    receipts=[]
    for case, cwd, label in CASES:
        path = BASE/f'copilot-agent-instructions-{label}-user-instructions.json'
        receipts.append(path)
        r = json.loads(path.read_text())
        assert r['passed'] and not r['full_adapter_support'] and (r['case'], r['cwd_subdir']) == (case, cwd)
        assert r['native_version'] == '1.0.84-9' and r['native_sha256'] == PINS['copilot']
        assert r['runner_sha256'] == sha(path.with_suffix('.runner.py'))
        assert r['source_unchanged'] and r['authority_unchanged'] and r['references_unchanged'] and r['roundtrip_preserved']
        assert set(r['definitions']) == ({'.claude/CLAUDE.md'} if case == 'dot-only' else {'AGENTS.md', 'CLAUDE.md', '.claude/CLAUDE.md', 'GEMINI.md', '.github/copilot-instructions.md'})
        assert set(r['references']) == {'root-policy.md', 'child-policy.md', 'claude-policy.md', '.claude/policy.md', '.github/policy.md', 'gemini-policy.md'}
        assert [p['label'] for p in r['phases']] == ['source', 'relocated']
        for phase in r['phases']: check_phase(phase, r)
        assert r['plan']['applicable']
        assert any('referenced project files' in action for action in r['plan']['native']['required_native_actions'])
        assert [c['command'][1] for c in r['commands']] == ['build', 'import', 'plan', 'apply', 'import']
        assert all(c['exit_code'] == 0 for c in r['commands'])
    for name in ('copilot-agent-instructions-first', 'copilot-agent-instructions-fallback-first', 'copilot-agent-instructions-dot-only-first'):
        path = BASE/(name+'.json')
        r = json.loads(path.read_text())
        assert not r['passed'] and r['native_sha256'] == PINS['copilot']
        assert r['runner_sha256'] == sha(path.with_suffix('.runner.py'))
        assert r['phases'][0]['instruction_markers']['.claude/CLAUDE.md'] is False
    state=summarize_receipts(receipts,ROOT,current_runner=ROOT/'WORKBENCH/conformance/run_native_copilot_agent_instructions.py',
                             current_helper=ROOT/'WORKBENCH/conformance/run_native_approvals.py')
    assert state['historical_integrity'],state['integrity_errors']
    print(json.dumps({'passed':True,'native_cases':4,**state,'full_adapter_support':False},indent=2))


if __name__ == '__main__':
    verify()
