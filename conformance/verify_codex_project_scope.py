#!/usr/bin/env python3
"""Verify effective Codex project restrictions and safe adapter handling."""
import argparse
import hashlib
import json
import re
from pathlib import Path

from evidence_state import assess_receipt

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'WORKBENCH/evidence/native-draft2-debug'
PIN = '3188814c35471432d4123203e0eb38e5bddc60226e3d7ddf0e59e649ea140022'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def phase_check(phase):
    assert phase['correlated'] and phase['turn']['status'] == 'completed'
    config = phase['effective_config']['config']
    assert config['model'] == 'project-model' and config['model_provider'] == 'user-fixture'
    assert 'project-fixture' not in config['model_providers']
    assert any(e.get('method') == 'turn/completed' and e['params']['threadId'] == phase['thread_id'] and e['params']['turn']['id'] == phase['turn']['id'] for e in phase['events'])
    assert phase['model_requests'] and all(r['endpoint'] == 'user' and r['body']['model'] == 'project-model' and phase['nonce'] in json.dumps(r['body']) for r in phase['model_requests'])


def verify(evidence_suffix='project-skills-final'):
    files = {str(p.relative_to(ROOT)): sha(p) for p in (ROOT / 'CLI/internal/config').glob('*.go')}
    eligibility = []
    for name, all_keys in (('provider', False), ('allkeys', True)):
        path = BASE / f'codex-project-{name}-{evidence_suffix}.json'
        record = json.loads(path.read_text())
        assert record['passed'] and record['all_keys'] is all_keys
        assert record['native_version'] == '0.154.0' and record['native_sha256'] == PIN
        assert record['runner_sha256'] == sha(path.with_suffix('.runner.py'))
        state = assess_receipt(path, ROOT, current_runner=ROOT/'WORKBENCH/conformance/run_native_codex_project_scope.py',
                               current_helper=ROOT/'WORKBENCH/conformance/run_native_approvals.py')
        assert state.integrity_valid, state.integrity_errors
        eligibility.append(state.current_eligible)
        assert record['required_refused_before_writes'] and record['user_config_unchanged'] and record['canonical_preserved']
        assert not record['full_adapter_support']
        assert record['imported_profile_required'] is False
        assert [p['label'] for p in record['phases']] == ['direct-project', 'optional-projection']
        for phase in record['phases']:
            phase_check(phase)
            if all_keys:
                config = phase['effective_config']['config']
                for key, value in record['project_values'].items():
                    if key != 'model': assert config.get(key) != value
                assert config['features']['respect_system_proxy'] is False
        ignored = [f for f in record['optional_plan']['native']['features'] if f['disposition'] == 'native-ignored']
        assert len(ignored) == (13 if all_keys else 2)
        assert all(f['activation'] == 'inactive' and f['native_status'] == 'native-effective-configuration' and f['evidence'] for f in ignored)
        failures = [c for c in record['commands'] if c['exit_code'] != 0]
        assert len(failures) == 1 and 'ignores project' in failures[0]['stderr']
    path = BASE / 'codex-project-provider-before.json'
    before = json.loads(path.read_text())
    assert not before['passed'] and 'adapter projected ignored project provider settings' in before['error']
    assert before['native_sha256'] == PIN and before['implementation_sha256'] != files
    assert before['runner_sha256'] == sha(path.with_suffix('.runner.py'))
    assert [p['label'] for p in before['phases']] == ['direct-project', 'incorrectly-projected']
    for phase in before['phases']: phase_check(phase)
    projection = json.loads((BASE / 'codex-project-provider-before-projection.json').read_text())
    assert projection['source_record_sha256'] == sha(path)
    assert projection['matches_original_project_source'] and projection['project_values'] == before['project_values']
    assert projection['project_values']['model_provider'] == 'project-fixture'
    sources = json.loads((BASE / 'codex-provider-scope.sources.json').read_text())
    assert sources['codex_source_revision'] == '6b9826e3aa83b1a5947db50f4332cb9c65f1b340'
    for source in sources['sources']: assert sha(BASE / source['file']) == source['sha256']
    loader = (BASE / 'codex-provider-scope-mod.source.txt').read_text()
    block = re.search(r'const PROJECT_LOCAL_CONFIG_DENYLIST:.*?= &\[(.*?)\];', loader, re.S).group(1)
    keys = set(re.findall(r'"([a-z_]+)"', block))
    all_fields = json.loads((BASE / 'codex-project-allkeys-verified.json').read_text())['project_values']
    assert keys == set(all_fields) - {'model', 'features'} and len(keys) == 12
    assert 'if features.remove("respect_system_proxy").is_some()' in loader
    return {'passed': True, 'native_cases': 2, 'completed_native_turns': 4, 'ignored_project_fields': 13,
            'scope': 'project', 'historical_integrity': True, 'current_support_eligible': all(eligibility),
            'full_adapter_support': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-suffix', choices=['project-skills-final', 'project-skills', 'skill-rechecked', 'skill-current', 'verified', 'role-current', 'role-checked', 'reference-current'], default='project-skills-final')
    print(json.dumps(verify(parser.parse_args().evidence_suffix), indent=2))
