#!/usr/bin/env python3
"""Verify that the earlier automatic-injection expectation was incorrect."""
import json
import hashlib

from evidence_state import summarize_receipts
from verify_copilot_recursive_instructions import BASE, PIN, ROOT, sha

CASES = [('mention', '**/*.go'), ('resource', '**/*.go'), ('tracked-view', '**/*.go'),
         ('second-turn', '**/*.go'), ('edit', '**/*.go'), ('edit-allowed', '**/*.go'),
         ('basename-glob', '*.go'), ('exact-name', 'fixture.go'), ('all-glob', '**/*')]


def verify():
    turns = 0
    receipts=[]
    for name, pattern in CASES:
        path = BASE / f'copilot-instruction-trigger-{name}-first.json'
        receipts.append(path)
        r = json.loads(path.read_text())
        assert r['native_version'] == '1.0.84-9' and r['native_sha256'] == PIN
        assert r['runner_sha256'] == sha(path.with_suffix('.runner.py'))
        assert not r['adapter_invoked'] and not r['full_adapter_support'] and r['pattern'] == pattern
        assert r['requests'] and r['turns']
        assert len(r['turns']) == (2 if name == 'second-turn' else 1)
        turns += len(r['turns'])
        for turn in r['turns']:
            assert turn['response']['stopReason'] == 'end_turn' and turn['nonce'] in json.dumps(r['requests'])
        for source in r['definitions'].values():
            assert hashlib.sha256(source['text'].encode()).hexdigest() == source['sha256']
        initial = '\n'.join(m.get('content', '') for m in r['requests'][0]['messages'] if m['role'] == 'system' and isinstance(m.get('content'), str))
        direct = name == 'all-glob'
        assert r['loaded'] == {'flat': direct, 'nested': direct}
        for tag in ('FLAT', 'NESTED'):
            assert ('AGENTS_TRIGGER_'+tag+'_BODY' in json.dumps([q['messages'] for q in r['requests']])) is direct
        if not direct:
            assert 'use the `view` tool to acquire it' in initial
            for file in ('flat.instructions.md', 'nested/deep/fixture.instructions.md'):
                assert f"| {pattern} | '.github/instructions/{file}' |  |" in initial
        events = r['native_events']
        assert any(e['type'] == 'session.start' and e['data']['sessionId'] == r['session']['sessionId']
                   and e['data']['copilotVersion'] == '1.0.84-9' for e in events)
        assert sum(e['type'] == 'assistant.turn_end' for e in events) == len(r['requests'])
        user_messages = [e['data'] for e in events if e['type'] == 'user.message']
        assert len(user_messages) == len(r['turns'])
        assert all(turn['nonce'] in event['content'] for turn, event in zip(r['turns'], user_messages))
        starts = [e['data'] for e in events if e['type'] == 'tool.execution_start']
        assert len(starts) == 1
        completed = [e['data'] for e in events if e['type'] == 'tool.execution_complete' and e['data']['toolCallId'] == starts[0]['toolCallId']]
        assert len(completed) == 1
        if name in ('edit', 'edit-allowed'):
            allowed = name == 'edit-allowed'
            assert starts[0]['toolName'] == 'apply_patch' and r['file'] in starts[0]['arguments']
            assert completed[0]['success'] is allowed
            assert r['effect'] == ('AGENTS_TRIGGER_FILE_AFTER\n' if allowed else 'AGENTS_TRIGGER_FILE_BEFORE\n')
            assert len(r['approvals']) == 1 and r['approvals'][0]['approved'] is allowed
            approval = r['approvals'][0]
            assert approval['params']['sessionId'] == r['session']['sessionId']
            assert approval['params']['toolCall']['toolCallId'] == starts[0]['toolCallId']
            assert approval['params']['toolCall']['rawInput']['fileName'] == r['file']
            assert approval['response']['outcome']['optionId'] == ('allow_once' if allowed else 'reject_once')
        else:
            assert starts[0]['toolName'] == 'view' and starts[0]['arguments']['path'] == r['file']
            assert completed[0]['success'] and 'AGENTS_TRIGGER_FILE_BEFORE' in completed[0]['result']['content']
            assert r['effect'] == 'AGENTS_TRIGGER_FILE_BEFORE\n' and r['file_observed'] and not r['approvals']
        assert r['passed'] is direct
        if not direct:
            assert ('native edit did not produce its file effect' if name == 'edit' else 'native instruction bodies differ from expected loading') in r['error']
    state=summarize_receipts(receipts,ROOT,current_runner=ROOT/'WORKBENCH/conformance/run_native_copilot_instruction_triggers.py',
                             current_helper=ROOT/'WORKBENCH/conformance/run_native_approvals.py')
    assert state['historical_integrity'],state['integrity_errors']
    return {'passed': True, 'native_cases': len(CASES), 'completed_native_turns': turns,
            'automatic_injection_expectation_corrected': True, 'adapter_invoked': False,
            **state, 'full_adapter_support': False}


if __name__ == '__main__':
    print(json.dumps(verify(), indent=2))
