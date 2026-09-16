"""Explicit deterministic inputs. No native client, account, or receipt is used.

These records test verifier assertions only. They omit implementation evidence
and must never be used as native receipts or release support evidence.
"""
import copy
import hashlib
import json
from pathlib import Path

from run_native_approvals import PINS
from synthetic_instruction_fixtures import instruction_phase

FIXTURE = '/synthetic-fixture'


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def snapshot(directory, name='synthetic'):
    path = Path(directory) / (name + '.json')
    path.with_suffix('.runner.py').write_text('# Synthetic test snapshot; not native evidence.\n')
    return path


def base_record(path, vendor='copilot'):
    return {'synthetic': True, 'passed': True, 'full_adapter_support': False,
            'fixture': FIXTURE, 'native_version': '0.154.0' if vendor == 'codex' else '1.0.84-9',
            'native_sha256': PINS[vendor],
            'runner_sha256': hashlib.sha256(path.with_suffix('.runner.py').read_bytes()).hexdigest()}


def commands(scope='user', count=3):
    return [{'command': ['/synthetic/agents', operation, '--scope', scope] +
                        (['--native-home', FIXTURE+'/home'] if scope == 'user' else []),
             'exit_code': 0, 'authority_before': {'trust': 'unchanged'},
             'authority_after': {'trust': 'unchanged'}}
            for _ in range(count) for operation in ('apply', 'import')]


def keymap_record(path):
    record = base_record(path, 'codex')
    record.update(scope='user', commands=commands(), phases=[])
    inputs = ['\x07', '\x1b[18~', '\x18\x05']
    for index, name in enumerate(('default', 'override', 'chord', 'unbound')):
        pid = 100 + index
        phase = {'name': name, 'pid': pid, 'alive_before_teardown': True,
                 'alive_after_teardown': False, 'raw_output': 'AGENTS_KEYMAP_EDITED',
                 'output': 'AGENTS_KEYMAP_EDITED', 'keys': []}
        for number, key in enumerate(inputs[:2] if index < 2 else inputs):
            effect = {'parent': pid, 'pid': pid+10, 'cwd': FIXTURE+'/workspace', 'time': 2} if number == index else None
            phase['keys'].append({'input': key, 'expected_effect': bool(effect), 'effect': effect, 'start': 1})
        if index:
            binding = {'global': {'open_external_editor': ('f7', 'ctrl-x ctrl-e', [])[index-1]}}
            for field in ('expected', 'projected', 'reimported'): phase[field] = copy.deepcopy(binding)
        record['phases'].append(phase)
    return record


def settings_record(path):
    record = base_record(path)
    record.update(commands=commands(), phases=[])
    for name, output in [('plan', ' · plan · Sessions [Current] Issues Pull requests ODA_STATUS_plan'),
                         ('interactive', '[Current] Sessions Issues Pull requests Gists ODA_STATUS_interactive'),
                         ('removed', '')]:
        preferences = {} if name == 'removed' else {'statusLine': {'refreshInterval': 2, 'command': 'synthetic-'+name}}
        phase = {'name': name, 'settings_mode': 0o600, 'state_unchanged_by_apply': True,
                 'settings_unchanged_by_native': True, 'alive_before_teardown': True,
                 'alive_after_teardown': False, 'raw_output': output, 'output': output,
                 'status_events': [], 'refresh_gaps': []}
        for field in ('preferences', 'projected_preferences', 'reimported_preferences'):
            phase[field] = copy.deepcopy(preferences)
        if name != 'removed':
            phase['status_events'] = [{'phase': name, 'recorded_at': n*2,
                'value': {'session_id': 'synthetic-'+name, 'cwd': FIXTURE+'/workspace',
                          'version': '1.0.84-9', 'model': {'id': 'fixture-model'}}} for n in range(3)]
            phase['refresh_gaps'] = [2, 2]
        record['phases'].append(phase)
    return record


def skill_phase(label='relocated', source='project', present=True,
                effect='AGENTS_SKILL_IMPORT_ASSET', package=None):
    phase = instruction_phase([], label)
    package = package or FIXTURE+'/target/.agents/skills/fixture-import'
    session = phase['session']['sessionId']
    phase.update(package=package, body_loaded=present, discovery=[], effect=None)
    phase['native_events'] = [e for e in phase['native_events'] if not e['type'].startswith('tool.')]
    if not present:
        phase['native_events'].append({'type': 'tool.execution_complete', 'data': {
            'success': False, 'error': {'message': 'Skill not found: fixture-import'}}})
        return phase
    phase['requests'][0]['messages'].append({'role': 'system', 'content': 'AGENTS_SKILL_IMPORT_BODY'})
    phase['discovery'] = [{'name': 'fixture-import', 'path': package, 'enabled': True, 'source': source}]
    command = '/usr/bin/python3 '+package+'/scripts/probe.py'
    phase['native_events'] += [
        {'type': 'skill.invoked', 'data': {'path': package+'/SKILL.md', 'trigger': 'agent-invoked', 'content': 'AGENTS_SKILL_IMPORT_BODY'}},
        {'type': 'tool.execution_start', 'data': {'toolName': 'bash', 'toolCallId': 'synthetic-shell', 'arguments': {'command': command}}},
        {'type': 'tool.execution_complete', 'data': {'toolCallId': 'synthetic-shell', 'success': True}}]
    phase['approvals'] = [{'approved': True, 'params': {'sessionId': session, 'toolCall': {
        'toolCallId': 'synthetic-shell', 'rawInput': {'command': command}}},
        'response': {'outcome': {'optionId': 'allow_once'}}}]
    phase['effect'] = effect
    return phase


def package_record():
    hashes = {name: digest('synthetic-'+name) for name in ('SKILL.md', 'data.txt', 'blob.bin', 'scripts/probe.py')}
    modes = {name: 0o700 if name.endswith('.py') else 0o600 for name in hashes}
    record = {'synthetic': True, 'origin': 'agents', 'source_unchanged': True, 'external_state_unchanged': True,
              'source_modes_preserved': True, 'source_inodes_preserved': True,
              'source_modes': modes, 'projected_modes': dict(modes),
              'phases': [skill_phase(label) for label in ('source', 'relocated')]}
    for key in ('source_hashes', 'imported_hashes', 'projected_hashes', 'reimported_hashes'):
        record[key] = dict(hashes)
    return record


def parent_skills_record(path):
    record = base_record(path)
    record.update(provider_errors=[], child_did_not_capture_parent=True, child_import_preserved_parent=True,
                  parent_unchanged_by_child=True, source_unchanged=True,
                  original_metadata={'mode': 0o700}, source_metadata_after={'mode': 0o700},
                  commands=commands(), phases=[])
    for field in ('original_hashes', 'imported_hashes', 'relocated_hashes'):
        record[field] = package_record()['source_hashes']
    for name in ('source', 'relocated', 'updated', 'override', 'fallback', 'nested-git'):
        prefix = FIXTURE+('/source' if name == 'source' else '/target')
        suffix = '/.github/skills/fixture-import' if name == 'source' else '/packages/child/.agents/skills/fixture-import' if name == 'override' else '/.agents/skills/fixture-import'
        effect = 'AGENTS_CHILD_OVERRIDE' if name == 'override' else 'AGENTS_PARENT_UPDATED' if name in ('updated', 'fallback') else 'AGENTS_SKILL_IMPORT_ASSET'
        phase = skill_phase(name, 'project' if name == 'override' else 'inherited', name != 'nested-git', effect, prefix+suffix)
        phase['workspace'] = prefix+'/packages/child'
        phase['events'].append({'session': phase['session']['sessionId'], 'message': 'AGENTS_PARENT_SKILL_DONE'})
        record['phases'].append(phase)
    record['parent_plan'] = {'native': {'features': [{'feature': 'artifact:skill-discovery:/Parent .github/skills/',
        'disposition': 'portable-mapping', 'source': FIXTURE+'/target/.agents/skills', 'destination': FIXTURE+'/target/.agents/skills'}]}}
    record['child_plan'] = {'native': {'features': [{'feature': 'skills:external-inherited-discovery',
        'disposition': 'external', 'ownership': 'not owned by this projection'}]}}
    return record


def recursive_phase(catalog=False):
    markers = ['AGENTS_FLAT_INSTRUCTION_BODY', 'AGENTS_NESTED_INSTRUCTION_BODY']
    phase = instruction_phase([] if catalog else markers, 'relocated')
    phase.update(file=phase['workspace']+'/fixture.txt', file_read=True, loaded={'flat': True, 'nested': True})
    if catalog:
        paths = ['.github/instructions/flat.instructions.md', '.github/instructions/nested/deep/fixture.instructions.md']
        phase['requests'][0]['messages'][0]['content'] = 'use the `view` tool to acquire it\n'+'\n'.join("| **/*.go | '"+p+"' |  |" for p in paths)
        phase['catalog'] = [{'pattern': '**/*.go', 'path': p, 'description': ''} for p in paths]
        phase['requests'][1]['messages'].append({'role': 'tool', 'content': ' '.join(markers)})
        reads = []
        for index, (path, marker) in enumerate(zip(paths, markers)):
            reads += [{'type': 'tool.execution_start', 'data': {'toolName': 'view', 'toolCallId': 'instruction-'+str(index), 'arguments': {'path': path}}},
                      {'type': 'tool.execution_complete', 'data': {'toolCallId': 'instruction-'+str(index), 'success': True, 'result': {'content': marker}}}]
        phase['native_events'] = reads+phase['native_events']
    return phase


def instruction_read_permission():
    return {'sessionId': 'synthetic-source', 'toolCall': {'toolCallId': 'read-fixture-1', 'kind': 'read',
        'rawInput': {'path': '/synthetic/instruction.md'}, 'locations': [{'path': '/synthetic/instruction.md'}]}}


def instruction_edit_permission():
    path = '/synthetic/fixture.go'
    diff = ('\ndiff --git a/synthetic/fixture.go b/synthetic/fixture.go\n'
            'index 0000000..0000000 100644\n--- a/synthetic/fixture.go\n+++ b/synthetic/fixture.go\n'
            '@@ -1,2 +1,2 @@\n-AGENTS_TRIGGER_FILE_BEFORE\n+AGENTS_TRIGGER_FILE_AFTER\n \n')
    return {'file': path, 'session': {'sessionId': 'synthetic-source'}, 'approvals': [{'params': {
        'sessionId': 'synthetic-source', 'toolCall': {'toolCallId': 'trigger-file', 'kind': 'edit',
        'locations': [{'path': path}], 'rawInput': {'fileName': path, 'diff': diff}}}}]}


def trust_record(path, case):
    record = base_record(path, 'codex')
    approved = case == 'post-start-disconnect'
    response = None if case == 'disconnect' else {'decision': 'accept' if approved else 'decline'}
    record.update(case=case, portable_security_projection_tested=False,
        approval_policy='omitted; trust-derived untrusted', config_unchanged=True,
        requests=[{'model': 'fixture-model'}], command_under_test=FIXTURE+'/workspace/first.py',
        thread={'approvalPolicy': 'untrusted', 'model': 'fixture-model'},
        first_effect=None, second_effect=None,
        approvals=[{'approved': approved, 'params': {'itemId': 'trust-boundary-call'}, 'response': response}],
        events=[{'method': 'item/completed', 'params': {'item': {'id': 'trust-boundary-call', 'status': 'completed' if approved else 'declined'}}}],
        completion={'params': {'turn': {'status': 'completed'}}})
    if case == 'unattended':
        record.update(effective_thread=dict(record['thread']), outcome='native-unattended-approval-bypass',
            native_events=[{'type': 'item.completed', 'item': {'type': 'command_execution', 'status': 'completed', 'exit_code': 0}}],
            first_effect='101', native_exit_code=0)
    elif case in ('composed-deny', 'background-deny'):
        record['command_under_test'] += (' && ' if case == 'composed-deny' else ' & ')+FIXTURE+'/workspace/second.py'
        record['outcome'] = 'verified-composed-denial-before-descendants'
    elif approved:
        record.update(outcome='detached-background-descendant-terminated-at-command-completion',
            first_effect_before_disconnect='101', first_effect='101', second_effect_before_disconnect=None,
            command_completed_before_disconnect=True, native_exit_after_disconnect=0)
    else:
        record.update(pending_request={'method': 'item/commandExecution/requestApproval', 'params': {'itemId': 'trust-boundary-call'}},
            observation_seconds=5, native_alive_after_window=True, first_effect_after_window=None,
            second_effect_after_window=None, native_exit_after_disconnect=0,
            outcome='no-native-approval-timeout-in-observation-window' if case == 'pending-timeout' else 'disconnect-before-approval-response-prevents-execution')
    return record


def local_network_record():
    return {'synthetic': True, 'evidence_class': 'native-settings-assessment',
        'portable_projection_tested': False, 'full_adapter_support': False,
        'copilot_sha256': PINS['copilot'], 'runner_sha256': digest('synthetic-runner'),
        'returncode': 0, 'probe_unchanged': True, 'correlated_native_execution': True,
        'deny_socket_path': '/synthetic/socket', 'host_tcp_reachable': True,
        'host_unix_reachable': True, 'host_abstract_reachable': True,
        'host_unix_marker_received': False, 'host_abstract_marker_received': False,
        'host_temporary_marker_created': False, 'local_network_mismatch_observed': True,
        'observations': {**{n: {'outcome': 'denied'} for n in ('network-1', 'network-2', 'host-abstract-unix')},
                         **{n: {'outcome': 'allowed'} for n in ('self-abstract-unix', 'self-loopback', 'self-loopback-ipv6', 'self-loopback-udp')}},
        'native_events': [{'type': 'tool.execution_start', 'data': {'toolCallId': 'synthetic-probe', 'command': 'python3 probe.py'}},
                          {'type': 'tool.execution_complete', 'data': {'toolCallId': 'synthetic-probe', 'success': True}}]}


def public_mcp_records(path, root):
    # Read tracked package provenance only; never read ignored evidence.
    provenance = json.loads((root/'.agents/plugins/com.openai.codex/provenance.json').read_text())
    names = ['get_me']+['synthetic_tool_'+str(i).zfill(2) for i in range(46)]
    direct = base_record(path)
    direct.update(tool_count=47, endpoint='https://api.githubcopilot.com/mcp/',
        operation='initialize and tools/list only', credential_source='gh auth keyring',
        credential_value_stored=False, credential_sha256_stored=False, remote_mutations=False,
        tool_names=names, requests=[{'method': m, 'status': 200, 'session_returned': True} for m in ('initialize', 'tools/list')])
    native = base_record(path, 'codex')
    native.update(public_endpoint=direct['endpoint'], credential_source='gh auth keyring',
        credential_value_stored=False, credential_sha256_stored=False, remote_mutations=False,
        external_github_mcp=True, external_model=False, source_revision=provenance['revision'],
        source_files=copy.deepcopy(provenance['files']), github_namespace_count=1,
        github_tool_count=47, direct_tool_count=47, direct_tool_names_match=True,
        github_tool_names=list(names), plugin_list={'installed': [{'pluginId': 'github@oda-public-github', 'installed': True, 'enabled': True}]},
        model_requests=[{'tools': [{'type': 'namespace', 'name': 'mcp__github', 'tools': [
            {'name': n, 'type': 'function', 'parameters': {}} for n in names]}]}],
        thread={'thread': {'id': 'synthetic-thread'}},
        terminal_mcp_startup={'threadId': 'synthetic-thread', 'name': 'github', 'status': 'ready'},
        mcp_ready_events=[{'threadId': 'synthetic-thread', 'name': 'github', 'status': 'ready'}],
        completion={'params': {'threadId': 'synthetic-thread', 'turn': {'status': 'completed'}, 'output': 'AGENTS_PUBLIC_GITHUB_READY'}},
        approvals=[], provider_errors=[], events=[])
    copilot = copy.deepcopy(native)
    copilot.update(native_version='1.0.84-9', native_sha256=PINS['copilot'],
        source_transformations=copy.deepcopy(provenance['transformations']),
        plugin_list={'stdout': 'github@agents-public-github'},
        model_requests=[{'tools': [{'type': 'function', 'function': {'name': 'github-'+n, 'parameters': {}}} for n in names]}],
        completion={'stopReason': 'end_turn'}, native_config_changed=True,
        native_first_launch_recorded=True, native_trusted_folders=[FIXTURE+'/workspace'])
    return direct, native, copilot


def skill_metadata_record(skill='allowed', trigger='model', interface='acp', decision='deny', malformed=False):
    names = ['baseline', 'hint', 'allowed', 'menu-hidden', 'model-hidden', 'both-hidden']
    if malformed:
        names = ['boolean-string', 'bad-yaml', 'plain-markdown', 'invalid-name', 'no-description', 'no-name']
        skill = None
    sources = {name: {'text': 'Synthetic package '+name, 'sha256': digest('Synthetic package '+name)} for name in names}
    visible = [] if malformed else ['baseline', 'hint', 'allowed', 'menu-hidden']
    features = []
    for name in names:
        activation = 'projection refused' if malformed else '; '.join(
            text for condition, text in [(name in ('model-hidden', 'both-hidden'), 'model invocation disabled'),
                                         (name in ('menu-hidden', 'both-hidden'), 'user invocation disabled')] if condition)
        features.append({'feature': 'skill', 'source': '/synthetic/fixture-'+name+'/SKILL.md', 'activation': activation})
    warnings = ['synthetic model invocation limit', 'synthetic user invocation limit']
    record = {'synthetic': True, 'full_adapter_support': False, 'probe_completed': True,
        'state_unchanged_by_apply': True, 'source_unchanged_by_apply': True,
        'sources': sources, 'target_hashes': {n: s['sha256'] for n, s in sources.items()},
        'cases': 'malformed' if malformed else 'controls', 'scope': 'project',
        'projection_refused': malformed, 'refusal_unchanged': True, 'native_setup_after_refusal': malformed,
        'commands': [{'command': ['/synthetic/agents', 'apply'], 'exit_code': 1 if malformed else 0,
                      'stdout': '\n'.join('warning\t'+w for w in warnings)}],
        'plan': {'native': {'features': features}, 'applicable': not malformed, 'actions': [], 'warnings': warnings},
        'nonce': 'synthetic-nonce', 'fixture': FIXTURE, 'interface': interface,
        'requests': [{'messages': [{'role': 'system', 'content': ' '.join('AGENTS_SKILL_DESCRIPTION_'+n for n in visible)},
                                  {'role': 'user', 'content': 'synthetic-nonce'}], 'tools': []}],
        'catalog_descriptions': {n: n in visible for n in names},
        'native_events': [{'type': 'session.start', 'data': {'sessionId': 'synthetic-session', 'copilotVersion': '1.0.84-9', 'context': {'cwd': FIXTURE+'/workspace'}}},
                          {'type': 'user.message', 'data': {'content': 'synthetic-nonce', 'turnId': 'synthetic-turn'}},
                          {'type': 'assistant.turn_end', 'data': {'turnId': 'synthetic-turn'}}],
        'discovery': [{'name': 'fixture-'+n, 'source': 'project', 'enabled': True} for n in names],
        'prompt': {'stopReason': 'end_turn'}, 'session': {'sessionId': 'synthetic-session'},
        'events': [{'params': {'update': {'sessionUpdate': 'available_commands_update', 'availableCommands': [
            {'name': 'fixture-'+n, 'input': {'hint': 'instructions for the skill'}} for n in ('baseline', 'hint', 'allowed', 'model-hidden')]}}}],
        'invoked_skill': skill, 'trigger': trigger, 'decision': decision, 'grant': '*',
        'shell_command': 'echo synthetic', 'command_style': 'builtin',
        'effect': 'AGENTS_SKILL_EFFECT' if decision == 'allow' else None}
    if malformed:
        record['discovery'] = [{'name': 'invalid_name', 'source': 'project', 'enabled': True},
            {'name': 'fixture-no-description', 'source': 'project', 'enabled': True, 'description': 'AGENTS_SKILL_BODY_no-description'},
            {'name': 'fixture-no-name', 'source': 'project', 'enabled': True, 'description': 'AGENTS_SKILL_DESCRIPTION_no-name'}]
        return record
    body = skill not in ('model-hidden', 'both-hidden') if trigger == 'model' else skill not in ('menu-hidden', 'both-hidden') and interface != 'cli'
    record['invoked_body_in_model'] = body
    if body:
        record['requests'][0]['messages'].append({'role': 'system', 'content': 'AGENTS_SKILL_BODY_'+skill})
        record['native_events'].append({'type': 'skill.invoked', 'data': {'name': 'fixture-'+skill, 'source': 'project',
            'content': 'AGENTS_SKILL_BODY_'+skill, 'trigger': 'user-invoked' if trigger == 'user' else 'agent-invoked', 'allowedTools': ['*']}})
    elif trigger == 'model':
        record['native_events'] += [{'type': 'tool.execution_start', 'data': {'toolName': 'skill', 'toolCallId': 'synthetic-skill'}},
            {'type': 'tool.execution_complete', 'data': {'toolCallId': 'synthetic-skill', 'success': False, 'error': {'message': 'Skill not found: fixture-'+skill}}}]
    record['native_events'] += [{'type': 'tool.execution_start', 'data': {'toolName': 'bash', 'toolCallId': 'synthetic-shell', 'arguments': {'command': 'echo synthetic'}}},
        {'type': 'tool.execution_complete', 'data': {'toolCallId': 'synthetic-shell', 'success': decision == 'allow',
         'error': {'code': 'denied' if interface == 'cli' else 'rejected', 'message': 'could not request permission'}}}]
    record['approvals'] = [{'approved': False, 'params': {'sessionId': 'synthetic-session', 'toolCall': {
        'toolCallId': 'synthetic-shell', 'rawInput': {'command': 'echo synthetic'}}}, 'response': {'outcome': {'optionId': 'reject_once'}}}]
    record['terminal'] = {'alive_before_teardown': True, 'alive_after_teardown': False,
        'effect_before_decision': False, 'decision': decision, 'phases': {
            'turn': 'Do you want to run this command? echo synthetic', 'hint': 'AGENTS_SKILL_DESCRIPTION_hint',
            'hint_selected': '', 'decision_result': 'Operation aborted by user'}}
    return record
