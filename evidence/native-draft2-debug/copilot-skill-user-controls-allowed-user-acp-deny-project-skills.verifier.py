#!/usr/bin/env python3
"""Verify bounded Copilot skill discovery, invocation, and approval evidence."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'WORKBENCH/evidence/native-draft2-debug'
PIN = 'a3262c4513ef1fc2ca21485261ca73196977ad76bd5e7990fb572f6134aaeedd'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def evaluate(record):
    """Assert effects and native events, independently of the runner verdict."""
    assert record['probe_completed']
    assert record['state_unchanged_by_apply'] and record['source_unchanged_by_apply']
    assert record['target_hashes'] == {name: source['sha256'] for name, source in record['sources'].items()}
    assert all(hashlib.sha256(source['text'].encode()).hexdigest() == source['sha256']
               for source in record['sources'].values())
    failed = [c for c in record['commands'] if c['exit_code'] != 0]
    skill_features = [f for f in record['plan']['native']['features'] if f['feature'] == 'skill']
    assert len(skill_features) == len(record['sources'])
    if record['cases'] == 'malformed':
        assert record['projection_refused'] and record['refusal_unchanged'] and record['native_setup_after_refusal']
        assert len(failed) == 1 and failed[0]['command'][1] == 'apply'
        assert not record['plan']['applicable'] and record['plan']['actions'] == []
        for name in ('boolean-string', 'bad-yaml', 'plain-markdown'):
            assert any('fixture-'+name+'/' in f['source'] and f['activation'] == 'projection refused' for f in skill_features)
    else:
        assert not failed and not record['projection_refused'] and record['plan']['applicable']
        assert len(record['plan']['warnings']) == 2
        applied = [c for c in record['commands'] if len(c['command']) > 1 and c['command'][1] == 'apply']
        assert len(applied) == 1
        assert all('warning\t'+warning in applied[0]['stdout'] for warning in record['plan']['warnings'])
        for name in ('model-hidden', 'both-hidden'):
            assert any('fixture-'+name+'/' in f['source'] and 'model invocation disabled' in f['activation'] for f in skill_features)
        for name in ('menu-hidden', 'both-hidden'):
            assert any('fixture-'+name+'/' in f['source'] and 'user invocation disabled' in f['activation'] for f in skill_features)
    if record['scope'] == 'user':
        assert record['imported_hashes'] == record['target_hashes']
        if record['cases'] == 'controls': assert record['reimported_hashes'] == record['target_hashes']
    assert record['requests'] and record['nonce'] in json.dumps(record['requests'])
    assert record['catalog_descriptions'] == {name: 'AGENTS_SKILL_DESCRIPTION_'+name in json.dumps(record['requests'][0])
                                               for name in record['sources']}
    events = record['native_events']
    starts = [e['data'] for e in events if e['type'] == 'session.start']
    assert len(starts) == 1 and starts[0]['copilotVersion'] == '1.0.83'
    assert starts[0]['context']['cwd'] == record['fixture']+'/workspace'
    users = [e['data'] for e in events if e['type'] == 'user.message' and record['nonce'] in e['data'].get('content', '')]
    assert len(users) == 1
    assert any(e['type'] == 'assistant.turn_end' and e['data']['turnId'] == users[0]['turnId'] for e in events)
    discovered = {s['name']: s for s in record['discovery'] if s['source'] != 'builtin'}
    expected = {'fixture-'+name for name in record['sources']}
    if record['cases'] == 'malformed':
        expected -= {'fixture-boolean-string', 'fixture-bad-yaml', 'fixture-plain-markdown', 'fixture-invalid-name'}
        expected.add('invalid_name')
    assert set(discovered) == expected
    if record['cases'] == 'malformed':
        assert discovered['fixture-no-description']['description'] == 'AGENTS_SKILL_BODY_no-description'
        assert discovered['fixture-no-name']['description'] == 'AGENTS_SKILL_DESCRIPTION_no-name'
    source = 'project' if record['scope'] == 'project' else 'personal-copilot'
    assert all(s['source'] == source and s['enabled'] for s in discovered.values())
    if record['cases'] == 'controls':
        assert record['catalog_descriptions'] == {
            'baseline': True, 'hint': True, 'allowed': True, 'menu-hidden': True,
            'model-hidden': False, 'both-hidden': False}
    if record['interface'] == 'acp':
        assert record['prompt']['stopReason'] == 'end_turn'
        assert starts[0]['sessionId'] == record['session']['sessionId']
        commands = [e['params']['update']['availableCommands'] for e in record['events']
                    if e.get('params', {}).get('update', {}).get('sessionUpdate') == 'available_commands_update']
        assert commands
        if record['cases'] == 'controls':
            for command_list in commands:
                skills = {c['name']: c for c in command_list if c['name'].startswith('fixture-')}
                assert set(skills) == {'fixture-baseline', 'fixture-hint', 'fixture-allowed', 'fixture-model-hidden'}
                assert skills['fixture-hint']['input']['hint'] == 'instructions for the skill'

    invoked = record['invoked_skill']
    invocations = [e['data'] for e in events if e['type'] == 'skill.invoked']
    if invoked:
        body_expected = (invoked not in ('model-hidden', 'both-hidden') if record['trigger'] == 'model'
                         else invoked not in ('menu-hidden', 'both-hidden') and record['interface'] != 'cli')
        assert record['invoked_body_in_model'] is body_expected
        body_seen = any('AGENTS_SKILL_BODY_'+invoked in json.dumps(r['messages']) for r in record['requests'])
        assert body_seen is body_expected
        assert len(invocations) == int(body_expected)
        if body_expected:
            invocation = invocations[0]
            assert invocation['name'] == 'fixture-'+invoked and invocation['source'] == source
            assert invocation['content'].strip() == 'AGENTS_SKILL_BODY_'+invoked
            assert invocation['trigger'] == ('user-invoked' if record['trigger'] == 'user' else 'agent-invoked')
            if invoked == 'allowed':
                assert invocation['allowedTools'] == [record['grant']]
        elif record['trigger'] == 'model':
            skill_starts = [e['data'] for e in events if e['type'] == 'tool.execution_start' and e['data']['toolName'] == 'skill']
            assert len(skill_starts) == 1
            failures = [e['data'] for e in events if e['type'] == 'tool.execution_complete'
                        and e['data']['toolCallId'] == skill_starts[0]['toolCallId']]
            assert len(failures) == 1 and failures[0]['success'] is False
            assert failures[0]['error']['message'] == 'Skill not found: fixture-'+invoked
        shell_starts = [e['data'] for e in events if e['type'] == 'tool.execution_start' and e['data']['toolName'] == 'bash']
        assert len(shell_starts) == 1 and shell_starts[0]['arguments']['command'] == record['shell_command']
        shell_id = shell_starts[0]['toolCallId']
        completions = [e['data'] for e in events if e['type'] == 'tool.execution_complete' and e['data']['toolCallId'] == shell_id]
        if record['interface'] == 'acp':
            approvals = record['approvals']
            assert len(approvals) == 1 and not approvals[0]['approved']
            assert approvals[0]['params']['sessionId'] == starts[0]['sessionId']
            assert approvals[0]['params']['toolCall']['toolCallId'] == shell_id
            assert approvals[0]['params']['toolCall']['rawInput']['command'] == record['shell_command']
            assert approvals[0]['response']['outcome']['optionId'] == 'reject_once'
            assert len(completions) == 1 and completions[0]['success'] is False
            assert completions[0]['error']['code'] == 'rejected'
        elif record['interface'] == 'cli':
            assert len(completions) == 1 and completions[0]['success'] is False
            assert completions[0]['error']['code'] == 'denied'
            assert 'could not request permission' in completions[0]['error']['message']
        else:
            terminal = record['terminal']
            assert terminal['alive_before_teardown'] and not terminal['alive_after_teardown']
            assert record['command_style'] == 'builtin'
            assert 'Do you want to run this command?' in terminal['phases']['turn']
            assert record['shell_command'] in terminal['phases']['turn']
            assert 'Allow directory access' not in terminal['phases']['turn']
            assert not terminal['effect_before_decision']
            assert terminal['decision'] == record['decision']
            assert 'AGENTS_SKILL_DESCRIPTION_hint' in terminal['phases']['hint']
            assert '[fixture-arg]' not in terminal['phases']['hint'] + terminal['phases']['hint_selected']
            if record['decision'] == 'allow':
                assert len(completions) == 1 and completions[0]['success'] is True
            else:
                # Escape cancels the turn. This is not a completed denial.
                assert 'Operation aborted by user' in terminal['phases']['decision_result']
                assert not any(c['success'] for c in completions)
    else:
        assert not invocations
    assert record['effect'] == ('AGENTS_SKILL_EFFECT' if record['decision'] == 'allow' else None)
    return {'discovery_verified': True, 'invocation_verified': bool(invoked),
            'permission_observation_verified': bool(invoked), 'full_adapter_support': False}


def matrix():
    cases = []
    for scope in ('project', 'user'):
        for trigger in ('model', 'user'):
            for skill in ('baseline', 'allowed', 'model-hidden', 'menu-hidden', 'both-hidden'):
                cases.append((scope, 'controls', skill, trigger, 'acp', 'deny'))
        cases.append((scope, 'malformed', None, 'model', 'acp', 'deny'))
        for decision in ('deny', 'allow'):
            cases.append((scope, 'controls', 'allowed', 'user', 'tui', decision))
        cases.append((scope, 'controls', 'allowed', 'model', 'cli', 'deny'))
    return cases


def filename(case):
    return 'copilot-skill-'+'-'.join(str(x or 'none') for x in case)+'-project-skills.json'


def verify():
    files = {str(p.relative_to(ROOT)): sha(p) for p in (ROOT / 'CLI/internal/config').glob('*.go')}
    for case in matrix():
        path = BASE / filename(case)
        record = json.loads(path.read_text())
        assert tuple(record[k] for k in ('scope', 'cases', 'invoked_skill', 'trigger', 'interface', 'decision')) == case
        assert record['passed'] and record['native_version'] == '1.0.83' and record['native_sha256'] == PIN
        assert record['grant'] == '*' and record['command_style'] == 'builtin'
        assert record['runner_sha256'] == sha(path.with_suffix('.runner.py')) == sha(ROOT / 'WORKBENCH/conformance/run_native_copilot_skill_metadata.py')
        assert record['helper_sha256'] == sha(ROOT / 'WORKBENCH/conformance/run_native_approvals.py')
        assert record['terminal_helper_sha256'] == sha(ROOT / 'WORKBENCH/conformance/run_native_copilot_preferences.py')
        assert record['verifier_sha256'] == sha(__file__) == sha(path.with_suffix('.verifier.py'))
        assert record['implementation_sha256'] == files and not record['full_adapter_support']
        assert evaluate(record) == record['observations']
    sources = json.loads((BASE / 'copilot-skill-frontmatter.sources.json').read_text())
    for source in sources['sources']:
        assert sha(BASE / source['file']) == source['sha256']
    return {'passed': True, 'native_cases': len(matrix()), 'full_adapter_support': False}


if __name__ == '__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(verify(), indent=2))
