#!/usr/bin/env python3
"""Exercise pinned Codex with a local deterministic Responses fixture."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import struct
import tempfile
import threading
import time
import zlib
from run_native_approvals import Client, PINS, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--scenario', choices=['discovery', 'agent-execution', 'trust-safe', 'trust-image', 'trust-rule-allow', 'trust-rule-deny'], default='discovery')
    parser.add_argument('--scope', choices=['project', 'user'], default='user')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Use a new evidence path')
    binary = shutil.which('codex')
    if not binary or sha(binary) != PINS['codex']:
        parser.error('Codex does not match the evidence pin')
    base = Path(tempfile.mkdtemp(prefix='oda-local-model-'))
    home, workspace = base / 'home', base / 'workspace'
    home.mkdir(mode=0o700); workspace.mkdir(mode=0o700)
    subprocess.run(['git', 'init', '--quiet', str(workspace)], check=True)
    requests = []
    responses = []
    root_step = 0
    child_step = 0
    trust_case = args.scenario.startswith('trust-')
    safe_file = workspace / 'safe-read.txt'
    safe_file.write_text('ODA_SAFE_READ_SENTINEL\n')
    probe = workspace / 'probe.py'
    probe.write_text('from pathlib import Path\nPath(__file__).with_name("rule-effect.txt").write_text("ODA_RULE_EFFECT")\n')
    image_file = workspace / 'fixture.png'
    def png_chunk(kind, body):
        return struct.pack('!I', len(body)) + kind + body + struct.pack('!I', zlib.crc32(kind + body))
    image_file.write_bytes(b'\x89PNG\r\n\x1a\n' + png_chunk(b'IHDR', struct.pack('!2I5B', 8, 8, 8, 2, 0, 0, 0))
                           + png_chunk(b'IDAT', zlib.compress((b'\x00' + bytes([1, 2, 3]) * 8) * 8)) + png_chunk(b'IEND', b''))
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_POST(self):
            nonlocal root_step, child_step
            raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            request = {'path': self.path, 'content_encoding': self.headers.get('Content-Encoding')}
            try:
                request['body'] = json.loads(raw)
            except Exception as error:
                request['error'] = str(error)
            requests.append(request)
            response_id = 'fixture-response-' + str(len(requests))
            events = [
                {'type': 'response.created', 'response': {'id': response_id}},
                {'type': 'response.output_item.done', 'item': {'type': 'message', 'role': 'assistant', 'id': 'fixture-message',
                    'content': [{'type': 'output_text', 'text': 'Fixture response complete.'}]}},
                {'type': 'response.completed', 'response': {'id': response_id,
                    'usage': {'input_tokens': 0, 'input_tokens_details': None, 'output_tokens': 0, 'output_tokens_details': None, 'total_tokens': 0}}},
            ]
            if args.scenario == 'agent-execution':
                native_input = json.dumps(request.get('body', {}).get('input', []))
                child = 'ODA_CHILD_CONFIGURATION_SENTINEL' in native_input
                call = None
                if child and child_step == 0:
                    child_step += 1
                    call = {'name': 'exec_command', 'arguments': {'cmd': 'printf native-agent-proof > agent-effect.txt', 'workdir': str(workspace)}}
                elif not child and root_step == 0:
                    root_step += 1
                    call = {'namespace': 'multi_agent_v1', 'name': 'spawn_agent', 'arguments': {'agent_type': 'fixture_reviewer', 'message': 'Execute the isolated fixture marker command.'}}
                elif not child and root_step == 1:
                    for item in request.get('body', {}).get('input', []):
                        if item.get('type') == 'function_call_output':
                            try:
                                output = json.loads(item.get('output', ''))
                            except (ValueError, TypeError):
                                continue
                            if output.get('agent_id'):
                                root_step += 1
                                call = {'namespace': 'multi_agent_v1', 'name': 'wait_agent', 'arguments': {'targets': [output['agent_id']], 'timeout_ms': 10000}}
                                break
                if call:
                    call['arguments'] = json.dumps(call['arguments'])
                    events[1] = {'type': 'response.output_item.done', 'item': {'type': 'function_call', 'call_id': 'fixture-call-' + str(len(requests)), **call}}
            elif trust_case and root_step == 0:
                root_step += 1
                command = 'cat ' + str(safe_file) if args.scenario == 'trust-safe' else '/usr/bin/python3 ' + str(probe)
                events[1] = {'type': 'response.output_item.done', 'item': {'type': 'function_call', 'call_id': 'fixture-trust-call', 'name': 'exec_command',
                    'arguments': json.dumps({'cmd': command, 'workdir': str(workspace)})}}
                if args.scenario == 'trust-image':
                    events[1]['item'].update(name='view_image', arguments=json.dumps({'path': str(image_file)}))
            responses.append(events)
            body = ''.join('event: ' + e['type'] + '\ndata: ' + json.dumps(e) + '\n\n' for e in events).encode()
            self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.send_header('Content-Length', str(len(body))); self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    (home / 'config.toml').write_text(
        'model = "fixture-model"\nmodel_provider = "fixture"\napproval_policy = "never"\nsandbox_mode = "workspace-write"\n'
        '[features]\nenable_request_compression = false\n'
        '[model_providers.fixture]\nname = "Local deterministic fixture"\n'
        f'base_url = "http://127.0.0.1:{server.server_port}/v1"\n'
        'wire_api = "responses"\nrequires_openai_auth = false\nsupports_websockets = false\n')
    if trust_case:
        native_config = (home / 'config.toml').read_text().replace('approval_policy = "never"\n', '')
        native_config += f'\n[projects.{json.dumps(str(workspace))}]\ntrust_level = "untrusted"\n'
        (home / 'config.toml').write_text(native_config)
        (workspace / '.codex').mkdir()
        (workspace / '.codex/config.toml').write_text('approval_policy = "never"\nmodel = "ODA_DISABLED_PROJECT_MODEL"\n')
        if args.scenario.startswith('trust-rule-'):
            (home / 'rules').mkdir()
            (home / 'rules/fixture.rules').write_text('prefix_rule(pattern=' + json.dumps(['/usr/bin/python3', str(probe)]) + ', decision="prompt")\n')
    elif args.scope == 'project':
        with (home / 'config.toml').open('a') as config_file:
            config_file.write(f'\n[projects.{json.dumps(str(workspace))}]\ntrust_level = "trusted"\n')
    user_config_before = (home / 'config.toml').read_bytes()
    agent = 'name = "fixture_reviewer"\ndescription = "ODA fixture agent discovery sentinel."\ndeveloper_instructions = "ODA_CHILD_CONFIGURATION_SENTINEL. Review only the isolated fixture."\nmodel = "fixture-child-model"\nmodel_reasoning_effort = "low"\n'
    canonical = (workspace if args.scope == 'project' else base / 'source') / '.agents'
    (canonical / 'native/com.openai.codex').mkdir(parents=True)
    (canonical / 'AGENTS.md').write_text('Use the isolated fixture.\n')
    (canonical / 'manifest.json').write_text(json.dumps({'version': '1.1.0-draft.2', 'profiles': ['native']}))
    (canonical / 'native/com.openai.codex/profile.json').write_text(json.dumps({'namespace': 'com.openai.codex', 'harness_version': '=0.154.0', 'scope': args.scope, 'required': True,
        'artifacts': [{'kind': 'agent', 'source': 'fixture.toml', 'name': 'fixture.toml'}]}))
    (canonical / 'native/com.openai.codex/fixture.toml').write_text(agent)
    cli = Path(__file__).resolve().parents[2] / 'CLI'
    projection_command = ['go', 'run', './cmd/agents', 'apply', '--vendor', 'codex', '--experimental', '--root', str(canonical.parent), '--scope', args.scope, '--format', 'json']
    if args.scope == 'user':
        projection_command += ['--native-home', str(home)]
    projection = subprocess.run(projection_command,
                                cwd=cli, capture_output=True, text=True, env={**os.environ, 'XDG_STATE_HOME': str(base / 'state')})
    for p in home.rglob('*'):
        if p.is_file(): p.chmod(0o600)
    result = {'native_version': '0.154.0', 'binary_sha256': sha(binary), 'fixture': str(base),
              'external_model': False, 'copied_credentials': False, 'full_adapter_support': False,
              'scope': 'local deterministic model transport and custom agent prompt discovery',
              'scenario': args.scenario, 'projection': {'returncode': projection.returncode, 'stdout': projection.stdout, 'stderr': projection.stderr},
              'projection_scope': args.scope, 'trust_fixture': 'trusted project entry' if args.scope == 'project' else 'isolated native user agent',
              'user_config_unchanged_by_projection': (home / 'config.toml').read_bytes() == user_config_before,
              'protocol_source': 'https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/core/tests/common/responses.rs'}
    client = Client([str(Path(binary).resolve()), 'app-server', '--stdio'], workspace,
                    {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'CODEX_HOME': str(home)}, 'allow' if args.scenario == 'trust-rule-allow' else 'deny', str(probe))
    deadline = time.monotonic() + 30
    try:
        if projection.returncode != 0:
            raise RuntimeError('Native agent projection failed')
        client.response(client.request('initialize', {'clientInfo': {'name': 'oda_local_model', 'version': '0.1.0'}}), deadline)
        client.send({'method': 'initialized', 'params': {}})
        started = client.response(client.request('thread/start', {'cwd': str(workspace), 'ephemeral': True}), deadline)
        result['thread'] = started
        client.response(client.request('turn/start', {'threadId': started['thread']['id'], 'input': [{'type': 'text', 'text': 'Return the fixture response.'}]}), deadline)
        while time.monotonic() < deadline:
            event = client.receive(deadline)
            if event.get('method') == 'turn/completed' and event.get('params', {}).get('threadId') == started['thread']['id']:
                result['completed_turn'] = event['params']; break
    except Exception as error:
        result['error'] = str(error)
    finally:
        client.close(); server.shutdown(); server.server_close(); thread.join(timeout=2)
        result['events'] = client.events; result['stderr'] = ''.join(client.errors); result['requests'] = requests; result['responses'] = responses
        result['approvals'] = client.approvals
    result['local_response_complete'] = result.get('completed_turn', {}).get('turn', {}).get('status') == 'completed' and bool(requests)
    result['agent_discovered_in_request'] = any('ODA fixture agent discovery sentinel.' in json.dumps(r.get('body', {})) for r in requests)
    marker = workspace / 'agent-effect.txt'
    result['agent_effect'] = marker.read_text() if marker.is_file() else None
    result['child_config_in_request'] = any('ODA_CHILD_CONFIGURATION_SENTINEL' in json.dumps(r.get('body', {}).get('input', [])) for r in requests)
    result['child_model_and_reasoning'] = any(
        'ODA_CHILD_CONFIGURATION_SENTINEL' in json.dumps(r.get('body', {}).get('input', []))
        and r.get('body', {}).get('model') == 'fixture-child-model'
        and r.get('body', {}).get('reasoning', {}).get('effort') == 'low' for r in requests)
    completed = [e['params'] for e in result['events'] if e.get('method') == 'item/completed']
    spawned = {thread_id for params in completed if params.get('item', {}).get('tool') == 'spawnAgent'
               and params['item'].get('status') == 'completed' for thread_id in params['item'].get('receiverThreadIds', [])}
    result['correlated_child_execution'] = any(
        params.get('threadId') in spawned and params.get('item', {}).get('type') == 'commandExecution'
        and params['item'].get('exitCode') == 0 and params['item'].get('status') == 'completed'
        and 'agent-effect.txt' in params['item'].get('command', '') for params in completed)
    result['scenario_passed'] = result['local_response_complete'] and result['agent_discovered_in_request']
    if args.scenario == 'agent-execution':
        result['scenario_passed'] = (result['scenario_passed'] and result['child_config_in_request']
                                     and result['child_model_and_reasoning'] and result['correlated_child_execution']
                                     and result['agent_effect'] == 'native-agent-proof')
    if trust_case:
        result['approval_policy_key_omitted'] = 'approval_policy' not in (home / 'config.toml').read_text()
        result['effective_untrusted'] = result.get('thread', {}).get('approvalPolicy') == 'untrusted'
        result['project_config_disabled'] = result.get('thread', {}).get('model') == 'fixture-model'
        terminals = [p['item'] for p in completed if p.get('item', {}).get('id') == 'fixture-trust-call' and p.get('item', {}).get('type') == 'commandExecution']
        result['trust_terminals'] = terminals
        result['rule_effect'] = (workspace / 'rule-effect.txt').read_text() if (workspace / 'rule-effect.txt').is_file() else None
        result['scenario_passed'] = result['local_response_complete'] and result['approval_policy_key_omitted'] and result['effective_untrusted'] and result['project_config_disabled']
        if args.scenario == 'trust-safe':
            result['safe_command_without_approval'] = not client.approvals and any(t.get('exitCode') == 0 and 'ODA_SAFE_READ_SENTINEL' in (t.get('aggregatedOutput') or '') for t in terminals)
            result['safe_command_approval_denied'] = any(a['params'].get('itemId') == 'fixture-trust-call' and not a['approved'] for a in client.approvals) and any(t.get('status') == 'declined' for t in terminals)
            result['scenario_passed'] = result['scenario_passed'] and result['safe_command_approval_denied']
        elif args.scenario == 'trust-image':
            result['image_items'] = [p['item'] for p in completed if p.get('item', {}).get('id') == 'fixture-trust-call']
            result['image_in_followup_request'] = any('data:image/' in json.dumps(r.get('body', {}).get('input', [])) for r in requests[1:])
            result['image_read_without_approval'] = not client.approvals and result['image_in_followup_request'] and bool(result['image_items'])
            result['scenario_passed'] = result['scenario_passed'] and result['image_read_without_approval']
        else:
            relevant = [a for a in client.approvals if a['params'].get('itemId') == 'fixture-trust-call']
            allowed = args.scenario == 'trust-rule-allow'
            result['scenario_passed'] = (result['scenario_passed'] and bool(relevant) and all(a['approved'] == allowed for a in relevant)
                                         and (result['rule_effect'] == 'ODA_RULE_EFFECT' if allowed else result['rule_effect'] is None)
                                         and any(t.get('status') == ('completed' if allowed else 'declined') for t in terminals))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    args.output.with_suffix('.runner.py').write_bytes(Path(__file__).read_bytes())
    print(json.dumps({k: result.get(k) for k in ['scenario_passed', 'local_response_complete', 'agent_discovered_in_request', 'child_config_in_request', 'agent_effect', 'error']}))
    return 0 if result['scenario_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
