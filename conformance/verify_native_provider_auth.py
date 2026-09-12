#!/usr/bin/env python3
"""Verify bounded native provider-auth selection and the retained failure."""
import argparse
import hashlib
import json
from pathlib import Path

from evidence_state import assess_receipt

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'WORKBENCH/evidence/native-draft2-debug'
PIN = '3188814c35471432d4123203e0eb38e5bddc60226e3d7ddf0e59e649ea140022'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def requests(phase):
    return [r for r in phase['requests'] if r['path'].endswith('/responses')]


def verify(evidence_suffix='project-skills-final'):
    files = {str(p.relative_to(ROOT / 'CLI')): sha(p) for p in (ROOT / 'CLI/internal/config').glob('*.go')}
    eligibility = []
    for auth in ('on', 'off'):
        path = BASE / f'codex-provider-auth-{auth}-{evidence_suffix}.json'
        record = json.loads(path.read_text())
        assert record['passed'] and record['auth'] == auth
        assert record['native_version'] == '0.154.0' and record['native_sha256'] == PIN
        assert record['runner_sha256'] == sha(path.with_suffix('.runner.py'))
        state = assess_receipt(path, ROOT/'CLI', current_runner=ROOT/'WORKBENCH/conformance/run_native_provider_auth.py',
                               current_helper=ROOT/'WORKBENCH/conformance/run_native_approvals.py')
        assert state.integrity_valid, state.integrity_errors
        eligibility.append(state.current_eligible)
        assert record['auth_before'] == record['auth_after'] and len(record['auth_after']) == 2
        assert record['imported_config']['model_providers']['fixture']['requires_openai_auth'] is (auth == 'on')
        assert not record['full_adapter_support']
        assert all(c['exit_code'] == 0 for c in record['commands'])
        assert [p['label'] for p in record['phases']] == ['source', 'relocated']
        for phase in record['phases']:
            assert phase['correlated'] and phase['exit_code'] == 0
            assert any(e['type'] == 'turn.completed' for e in phase['events'])
            rows = requests(phase)
            assert rows and all(r['authorized'] is (auth == 'on') for r in rows)
            assert all('ODA_PROVIDER_AUTH_' in json.dumps(r['body']) for r in rows)
        assert 'oda-synthetic-model-auth' not in json.dumps(record), 'credential in evidence'
    path = BASE / 'codex-provider-auth-baseline.json'
    before = json.loads(path.read_text())
    assert not before['passed'] and 'native account authentication changed' in before['error']
    assert before['native_sha256'] == PIN and before['auth'] == 'on'
    assert before['runner_sha256'] == sha(path.with_suffix('.runner.py'))
    assert before['implementation_sha256'] != files
    assert [p['label'] for p in before['phases']] == ['source', 'relocated']
    for index, phase in enumerate(before['phases']):
        assert phase['exit_code'] == 0 and any(e['type'] == 'turn.completed' for e in phase['events'])
        rows = requests(phase)
        assert rows and all(r['authorized'] is (index == 0) for r in rows)
    sources = json.loads((BASE / 'codex-provider-auth.sources.json').read_text())
    assert sources['codex_source_revision'] == '6b9826e3aa83b1a5947db50f4332cb9c65f1b340'
    for source in sources['sources']:
        assert sha(BASE / source['file']) == source['sha256']
    return {'passed': True, 'native_cases': 2, 'native_phases': 4, 'retained_native_failure': True,
            'scope': 'user', 'historical_integrity': True, 'current_support_eligible': all(eligibility),
            'full_adapter_support': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-suffix', choices=['project-skills-final', 'project-skills', 'skill-serial', 'skill-rechecked', 'skill-current', 'verified', 'command-current', 'scope-current', 'role-current', 'role-checked', 'reference-current'], default='project-skills-final')
    print(json.dumps(verify(parser.parse_args().evidence_suffix), indent=2))
