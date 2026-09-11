#!/usr/bin/env python3
"""Verify token-command preservation and its native authentication limits."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'WORKBENCH/evidence/native-draft2-debug'
PIN = '3188814c35471432d4123203e0eb38e5bddc60226e3d7ddf0e59e649ea140022'
SUCCESSES = ('success', 'cache', 'refresh', 'retry')
FAILURES = {'timeout': 'timed out', 'empty': 'empty token', 'exit': 'exited with status', 'invalid-utf8': 'non-UTF-8', 'missing': 'failed to start'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def model_requests(phase):
    return [r for r in phase['requests'] if r['path'].endswith('/responses')]


def verify(evidence_suffix='project-skills-final'):
    files = {str(p.relative_to(ROOT / 'CLI')): sha(p) for p in (ROOT / 'CLI/internal/config').glob('*.go')}
    turns = 0
    for case in (*SUCCESSES, *FAILURES):
        path = BASE / f'codex-command-auth-{case}-{evidence_suffix}.json'
        record = json.loads(path.read_text())
        assert record['passed'] and record['case'] == case
        assert record['native_version'] == '0.154.0' and record['native_sha256'] == PIN
        assert record['runner_sha256'] == sha(path.with_suffix('.runner.py')) == sha(ROOT / 'WORKBENCH/conformance/run_native_command_auth.py')
        assert record['helper_sha256'] == sha(ROOT / 'WORKBENCH/conformance/run_native_approvals.py')
        assert record['token_helper_sha256'] == sha(path.with_suffix('.token.py'))
        assert record['implementation_sha256'] == files
        assert record['observe_failure_fallback'] is (case in FAILURES)
        assert not record['authentication_enforcement_verified'] and not record['full_adapter_support']
        assert all(record[k] for k in ('adapter_did_not_execute', 'external_helper_unchanged', 'no_helper_copied', 'no_auth_files', 'source_config_unchanged'))
        assert all(c['exit_code'] == 0 for c in record['commands'])
        assert [p['label'] for p in record['phases']] == ['source', 'relocated']
        auth = record['imported_config']['model_providers']['fixture']['auth']
        assert auth['args'] == ['token.py', case, 'literal;argument', '$(literal)']
        assert auth['cwd'] == str(Path(record['fixture']) / 'command')
        assert auth['timeout_ms'] == (100 if case == 'timeout' else 1000)
        assert auth['refresh_interval_ms'] == (50 if case == 'refresh' else 0)
        for phase in record['phases']:
            assert phase['correlated']
            rows = model_requests(phase)
            assert rows and all('ODA_COMMAND_AUTH_' in json.dumps(r['body']) for r in rows)
            if case in ('cache', 'refresh'):
                assert phase['native_alive_before_close'] and len(phase['turns']) == 2
                assert all(t['status'] == 'completed' for t in phase['turns'])
                completed = [e['params']['turn']['id'] for e in phase['events'] if e.get('method') == 'turn/completed' and e['params']['threadId'] == phase['thread_id']]
                assert completed == [t['id'] for t in phase['turns']]
                a, b = phase['calls_after_turn']
                assert a >= 1 and (b == a if case == 'cache' else b > a)
                assert len(rows) >= 2
                turns += 2
            else:
                assert phase['exit_code'] == 0 and any(e['type'] == 'turn.completed' for e in phase['events'])
                turns += 1
            if case in FAILURES:
                assert phase['native_unauthenticated_fallback'] and FAILURES[case] in phase['stderr']
                assert all(not r['authorized'] for r in rows)
            else:
                assert all(r['authorized'] for r in rows)
            if case == 'retry':
                assert rows[0]['status'] == 401 and rows[-1]['status'] == 200
                assert rows[-1]['token_revision'] > rows[0]['token_revision']
            if case == 'missing':
                assert not phase['calls']
            else:
                assert phase['calls'] and all(c == {'cwd': auth['cwd'], 'args': auth['args'][1:]} for c in phase['calls'])
        assert 'oda-command-synthetic-token' not in json.dumps(record), 'token value in evidence'
    before = json.loads((BASE / 'codex-command-auth-native-before.json').read_text())
    assert not before['passed'] and len(before['phases']) == 1
    assert before['phases'][0]['correlated'] and all(r['authorized'] for r in model_requests(before['phases'][0]))
    assert before['commands'][-1]['exit_code'] != 0 and 'excluded credential or authority' in before['commands'][-1]['stderr']
    regression_path = BASE / 'codex-command-auth-before.json'
    regression = json.loads(regression_path.read_text())
    assert regression['exit_code'] != 0 and 'valid token-command configuration was rejected' in regression['stdout']
    assert regression['test_sha256'] == sha(regression_path.with_suffix('.test.go'))
    for case in FAILURES:
        first = json.loads((BASE / f'codex-command-auth-{case}-first.json').read_text())
        assert not first['passed'] and 'failed token command did not prevent model requests' in first['error']
        rows = model_requests(first['phases'][0])
        assert rows and all(not r['authorized'] for r in rows)
    sources = json.loads((BASE / 'codex-command-auth.sources.json').read_text())
    assert sources['codex_source_revision'] == '6b9826e3aa83b1a5947db50f4332cb9c65f1b340'
    for source in sources['sources']:
        assert sha(BASE / source['file']) == source['sha256']
    return {'passed': True, 'native_cases': 9, 'native_processes': 18, 'completed_native_turns': turns,
            'scope': 'user', 'native_authentication_fallback_cases': 5, 'authentication_enforcement_verified': False,
            'full_adapter_support': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-suffix', choices=['project-skills-final', 'project-skills', 'skill-rechecked', 'skill-current', 'verified', 'scope-current', 'role-current', 'role-checked', 'reference-current'], default='project-skills-final')
    print(json.dumps(verify(parser.parse_args().evidence_suffix), indent=2))
