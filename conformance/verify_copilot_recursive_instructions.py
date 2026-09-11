#!/usr/bin/env python3
"""Verify direct loading, native instruction catalogs, and explicit read permission."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'WORKBENCH/evidence/native-draft2-debug'
PIN = 'a3262c4513ef1fc2ca21485261ca73196977ad76bd5e7990fb572f6134aaeedd'
CASES = [('project', 'all', 'yes', '**', False, 'deny'), ('user', 'all', 'yes', '**', False, 'deny'),
         ('project', 'nonmatching', 'no', '**/*.go', False, 'deny'), ('user', 'nonmatching', 'no', '**/*.go', False, 'deny'),
         ('project', 'catalog', 'yes', '**/*.go', True, 'deny'),
         ('user', 'catalog-allow', 'yes', '**/*.go', True, 'allow'),
         ('user', 'catalog-deny', 'yes', '**/*.go', True, 'deny')]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_catalog(phase, paths):
    context = '\n'.join(m.get('content', '') for m in phase['requests'][0]['messages'] if m['role'] == 'system' and isinstance(m.get('content'), str))
    assert 'use the `view` tool to acquire it' in context
    assert 'AGENTS_FLAT_INSTRUCTION_BODY' not in context and 'AGENTS_NESTED_INSTRUCTION_BODY' not in context
    for path in paths: assert f"| **/*.go | '{path}' |  |" in context
    if 'catalog' in phase:
        assert len(phase['catalog']) == len(paths)
        assert {(r['pattern'], r['path'], r['description']) for r in phase['catalog']} == {('**/*.go', p, '') for p in paths}


def check_phase(phase, flat, nested, instruction_reads=(), approval=None):
    assert phase['prompt']['stopReason'] == 'end_turn'
    assert len(phase['requests']) >= (1 if approval == 'deny' else 2) and phase['nonce'] in json.dumps(phase['requests'])
    assert phase['loaded'] == {'flat': flat, 'nested': nested}
    context = json.dumps([r['messages'] for r in phase['requests']])
    assert ('AGENTS_FLAT_INSTRUCTION_BODY' in context) is flat
    assert ('AGENTS_NESTED_INSTRUCTION_BODY' in context) is nested
    assert phase['file_read'] is ('AGENTS_FILE_READ' in context)
    if approval != 'deny': assert phase['file_read']
    events = phase['native_events']
    assert any(e['type'] == 'session.start' and e['data']['sessionId'] == phase['session']['sessionId']
               and e['data']['copilotVersion'] == '1.0.83' and e['data']['context']['cwd'] == phase['workspace'] for e in events)
    assert any(e['type'] == 'user.message' and phase['nonce'] in e['data']['content'] for e in events)
    reads = [e['data'] for e in events if e['type'] == 'tool.execution_start' and e['data']['toolName'] == 'view']
    expected_reads = [*instruction_reads, phase['file']]
    assert reads
    assert [r['arguments']['path'] for r in reads] == (expected_reads[:len(reads)] if approval == 'deny' else expected_reads)
    if reads[-1]['arguments']['path'] == phase['file']:
        assert any(e['type'] == 'tool.execution_complete' and e['data']['toolCallId'] == reads[-1]['toolCallId']
                   and e['data']['success'] is True and 'AGENTS_FILE_READ' in e['data']['result']['content'] for e in events)
    for index, read in enumerate(r for r in reads if r['arguments']['path'] in instruction_reads):
        completed = [e['data'] for e in events if e['type'] == 'tool.execution_complete' and e['data']['toolCallId'] == read['toolCallId']]
        assert len(completed) == 1 and completed[0]['success'] is (approval != 'deny')
        if approval != 'deny':
            marker = 'AGENTS_'+('FLAT' if index == 0 else 'NESTED')+'_INSTRUCTION_BODY'
            assert marker in completed[0]['result']['content']
    if approval is None:
        assert not phase['approvals']
    else:
        assert phase['approvals']
        if approval == 'allow': assert len(phase['approvals']) == len(instruction_reads)
        for item in phase['approvals']:
            params, call = item['params'], item['params']['toolCall']
            assert params['sessionId'] == phase['session']['sessionId'] and call['kind'] == 'read'
            assert call['rawInput']['path'] in instruction_reads
            read = next(r for r in reads if r['toolCallId'] == call['toolCallId'])
            assert read['arguments']['path'] == call['rawInput']['path']
            assert item['approved'] is (approval == 'allow')
            assert item['response']['outcome']['optionId'] == ('allow_once' if approval == 'allow' else 'reject_once')
    assert any(e['type'] == 'assistant.turn_end' for e in events)
    assert any(e.get('method') == 'session/update' and e['params']['sessionId'] == phase['session']['sessionId'] for e in phase['events'])


def verify(suffix='user-instructions'):
    implementation = {str(p.relative_to(ROOT)): sha(p) for p in (ROOT / 'CLI/internal/config').glob('*.go')}
    for scope, label, match, pattern, follow, decision in CASES:
        path = BASE / f'copilot-recursive-instructions-{scope}-{label}-{suffix}.json'
        r = json.loads(path.read_text())
        assert r['passed'] and not r['full_adapter_support']
        assert (r['scope'], r['match'], r['pattern']) == (scope, match, pattern)
        assert r['follow_catalog'] is follow and r['instruction_approval'] == decision
        assert r['native_version'] == '1.0.83' and r['native_sha256'] == PIN
        assert r['runner_sha256'] == sha(path.with_suffix('.runner.py')) == sha(ROOT / 'WORKBENCH/conformance/run_native_copilot_recursive_instructions.py')
        assert r['helper_sha256'] == sha(ROOT / 'WORKBENCH/conformance/run_native_approvals.py')
        assert r['implementation_sha256'] == implementation
        expected = {name: hashlib.sha256(f'---\napplyTo: "{pattern}"\n---\nAGENTS_{kind}_INSTRUCTION_BODY\n'.encode()).hexdigest()
                    for name, kind in [('flat.instructions.md', 'FLAT'), ('nested/deep/fixture.instructions.md', 'NESTED')]}
        assert r['source_hashes'] == r['imported_hashes'] == r['projected_hashes'] == r['reimported_hashes'] == expected
        assert r['source_unchanged'] and r['external_state_unchanged'] and r['plan']['applicable']
        actions = r['plan']['native']['required_native_actions']
        assert any('native read permission for path-specific user instructions' in a for a in actions) is (scope == 'user')
        assert all(c['exit_code'] == 0 for c in r['commands'])
        assert [p['label'] for p in r['phases']] == ['source', 'relocated']
        for phase in r['phases']:
            assert phase['workspace'] == r['fixture'] + ('/source' if phase['label'] == 'source' else '/target')
            assert phase['file'] == phase['workspace'] + ('/fixture.go' if match == 'yes' else '/fixture.txt')
            paths = [('.github/instructions/' if scope == 'project' else r['fixture']+('/source-home/instructions/' if phase['label'] == 'source' else '/target-home/instructions/'))+name
                     for name in ('flat.instructions.md', 'nested/deep/fixture.instructions.md')]
            if pattern == '**/*.go': check_catalog(phase, paths)
            expected = match == 'yes' and (not follow or scope == 'project' or decision == 'allow')
            check_phase(phase, expected, expected, paths if follow else (), decision if follow and scope == 'user' else None)
    for scope in ('project', 'user'):
        path = BASE / f'copilot-recursive-instructions-{scope}-all-before.json'
        before = json.loads(path.read_text())
        assert not before['passed'] and 'recursive instruction assets were lost during import' in before['error']
        assert before['native_sha256'] == PIN and before['implementation_sha256'] != implementation
        assert before['runner_sha256'] == sha(path.with_suffix('.runner.py'))
        assert set(before['imported_hashes']) == {'flat.instructions.md'}
        check_phase(before['phases'][0], True, True)
        check_phase(before['phases'][1], True, False)
    path = BASE / 'copilot-recursive-instructions-project-yes-before.json'
    limitation = json.loads(path.read_text())
    assert not limitation['passed'] and 'source instruction selection differs' in limitation['error']
    assert limitation['native_sha256'] == PIN and limitation['runner_sha256'] == sha(path.with_suffix('.runner.py'))
    assert len(limitation['phases']) == 1 and limitation['phases'][0]['file'].endswith('/fixture.go')
    check_phase(limitation['phases'][0], False, False)
    check_catalog(limitation['phases'][0], ['.github/instructions/flat.instructions.md', '.github/instructions/nested/deep/fixture.instructions.md'])
    for name in ('copilot-recursive-instructions-user-catalog-first.json',
                 'copilot-recursive-instructions-user-catalog-deny-catalog-final.json'):
        path = BASE / name
        denied = json.loads(path.read_text())
        assert not denied['passed'] and len(denied['phases']) == 1 and 'AssertionError' in denied['error']
        assert denied['native_sha256'] == PIN and denied['runner_sha256'] == sha(path.with_suffix('.runner.py'))
        phase = denied['phases'][0]
        paths = [denied['fixture']+'/source-home/instructions/'+name for name in ('flat.instructions.md', 'nested/deep/fixture.instructions.md')]
        check_catalog(phase, paths)
        check_phase(phase, False, False, paths, 'deny')
    path = BASE / 'copilot-recursive-instructions-go-before.json'
    before = json.loads(path.read_text())
    assert before['exit_code'] != 0 and before['stdout'].count('nested/deep/same.instructions.md: no such file or directory') == 2
    assert before['test_sha256'] == sha(path.with_suffix('.test.go'))
    before = json.loads((BASE / 'copilot-recursive-instructions-import-before.json').read_text())
    assert not before['passed'] and len(before['cases']) == 4
    for case in before['cases']:
        assert (case['exit_code'] == 0) == (case['nesting'] == 'flat')
        assert len(case['imported']) == int(case['nesting'] == 'flat')
    sources = json.loads((BASE / 'copilot-recursive-instructions.sources.json').read_text())
    for source in sources['sources']: assert sha(BASE / source['file']) == source['sha256']
    return {'passed': True, 'native_cases': 7, 'native_processes': 14, 'completed_native_turns': 14,
            'scope': 'project|user', 'model_catalog_verified': True,
            'path_matching_verified': False, 'full_adapter_support': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-suffix', default='user-instructions')
    print(json.dumps(verify(parser.parse_args().evidence_suffix), indent=2))
