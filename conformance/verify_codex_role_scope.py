#!/usr/bin/env python3
"""Verify native role limits, atomic refusal, and retained failures."""
import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'WORKBENCH/evidence/native-draft2-debug'
PIN = '3188814c35471432d4123203e0eb38e5bddc60226e3d7ddf0e59e649ea140022'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def phase_check(phase, case='provider'):
    assert phase['correlated'] and phase['turn']['status'] == 'completed'
    assert any(e.get('method') == 'turn/completed' and e['params']['threadId'] == phase['parent_id']
               and e['params']['turn']['id'] == phase['turn']['id'] for e in phase['events'])
    completed = [e['params'] for e in phase['events'] if e.get('method') == 'item/completed']
    spawned = {tid for p in completed if p.get('item', {}).get('tool') == 'spawnAgent'
               and p['item'].get('status') == 'completed' for tid in p['item'].get('receiverThreadIds', [])}
    assert spawned and any(e.get('method') == 'turn/completed' and e['params'].get('threadId') in spawned
                           and e['params']['turn']['status'] == 'completed' for e in phase['events'])
    children = [r for r in phase['requests'] if 'AGENTS_CHILD_CONFIGURATION_SENTINEL' in json.dumps(r['body'].get('input', []))]
    assert children and all(r['path'] == '/parent/v1/responses' and r['body']['model'] == 'child-model'
                            and r['body']['reasoning']['effort'] == 'low' and phase['nonce'] in json.dumps(r['body']) for r in children)
    if case != 'provider':
        def enabled(request):
            if case == 'shell': return any(t.get('name') == 'exec_command' for t in request['body'].get('tools', []))
            return 'AGENTS_ROLE_SKILL_DESCRIPTION' in json.dumps(request['body'].get('input', []))
        parents = [r for r in phase['requests'] if r not in children]
        assert parents and all(enabled(r) == (phase['label'] == 'supported-role-projected') for r in parents)
        assert all(not enabled(r) for r in children)


def verify(evidence_suffix='project-skills-final'):
    files = {str(p.relative_to(ROOT)): sha(p) for p in (ROOT / 'CLI/internal/config').glob('*.go')}
    for scope in ('project', 'user'):
        for case in ('provider', 'shell', 'skills', 'selector'):
            path = BASE / f'codex-role-{case}-{scope}-{evidence_suffix}.json'
            record = json.loads(path.read_text())
            assert record['passed'] and record['scope'] == scope and record['case'] == case
            assert record['native_version'] == '0.154.0' and record['native_sha256'] == PIN
            assert record['runner_sha256'] == sha(path.with_suffix('.runner.py')) == sha(ROOT / 'WORKBENCH/conformance/run_native_codex_role_scope.py')
            assert record['helper_sha256'] == sha(ROOT / 'WORKBENCH/conformance/run_native_approvals.py')
            assert record['implementation_sha256'] == files
            assert record['required_refused_before_writes'] and record['optional_preserved_inactive'] and record['user_config_unchanged']
            assert not record['full_adapter_support']
            assert [p['label'] for p in record['phases']] == ['direct-role-provider-ignored', 'supported-role-projected']
            for phase in record['phases']: phase_check(phase, case)
            key = {'provider': 'model_provider', 'shell': 'features.shell_tool', 'skills': 'skills.include_instructions', 'selector': 'skills.config'}[case]
            ignored = [f for f in record['optional_plan']['native']['features'] if key in f.get('limitation', '')]
            assert len(ignored) == 1 and ignored[0]['activation'] == 'inactive'
            failures = [c for c in record['commands'] if c['exit_code'] != 0]
            assert len(failures) == 1 and key in failures[0]['stderr']
    path = BASE / 'codex-role-user-before.json'
    before = json.loads(path.read_text())
    assert not before['passed'] and 'adapter activated an ignored role provider' in before['error']
    assert before['native_sha256'] == PIN and before['implementation_sha256'] != files
    assert before['runner_sha256'] == sha(path.with_suffix('.runner.py'))
    assert "model_provider='child'" in before['incorrectly_projected_role']
    for phase in before['phases']: phase_check(phase)
    regression = json.loads((BASE / 'codex-role-validation-before.json').read_text())
    assert regression['exit_code'] != 0 and 'ignored role override activated' in regression['stdout']
    assert regression['test_sha256'] == sha(BASE / 'codex-role-validation-before.test.go')
    for name, text in [('shell', 'duplicate key'), ('skills', 'manifest.json')]:
        path = BASE / f'codex-role-{name}-project-first.json'
        first = json.loads(path.read_text())
        assert not first['passed'] and text in first['error']
        assert first['runner_sha256'] == sha(path.with_suffix('.runner.py'))
    for case in ('skills', 'selector'):
        path = BASE / f'codex-role-{case}-user-final.json'
        first = json.loads(path.read_text())
        assert not first['passed'] and 'setting has no ownership record' in first['error']
        assert first['runner_sha256'] == sha(path.with_suffix('.runner.py'))
    path = BASE / 'codex-role-selector-user-verified.json'
    duplicate = json.loads(path.read_text())
    assert not duplicate['passed'] and 'reduction failed' in duplicate['error']
    assert duplicate['runner_sha256'] == sha(path.with_suffix('.runner.py'))
    path = BASE / 'codex-role-selector-user-reference-current.json'
    selector_import = json.loads(path.read_text())
    assert not selector_import['passed'] and 'canonical.read_text() == ignored_role' in selector_import['error']
    assert selector_import['runner_sha256'] == sha(path.with_suffix('.runner.py'))
    for name in ('codex-agent-role-scope.sources.json', 'codex-role-docs.sources.json', 'codex-role-audit.sources.json'):
        sources = json.loads((BASE / name).read_text())
        for source in sources['sources']: assert sha(BASE / source['file']) == source['sha256']
    source = (BASE / 'codex-agent-role-scope-codex-rs-core-src-agent-role.source.txt').read_text()
    block = re.search(r'struct AgentRoleOverrides \{(.*?)\n\}', source, re.S).group(1)
    fields = set(re.findall(r'^    (\w+):', block, re.M))
    assert fields == {'developer_instructions', 'model', 'model_reasoning_effort', 'model_reasoning_summary', 'model_verbosity', 'personality', 'service_tier', 'features', 'skills'}
    assert 'skills.config.retain(|skill| !skill.enabled)' in source and 'skills.max_context_tokens = None' in source
    return {'passed': True, 'native_cases': 8, 'native_processes': 16, 'completed_native_turns': 32,
            'scope': 'project|user', 'full_adapter_support': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-suffix', choices=['project-skills-final', 'project-skills', 'skill-rechecked', 'skill-current', 'checked', 'reference-current', 'reference-corrected'], default='project-skills-final')
    print(json.dumps(verify(parser.parse_args().evidence_suffix), indent=2))
