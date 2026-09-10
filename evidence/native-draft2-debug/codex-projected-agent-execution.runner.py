#!/usr/bin/env python3
"""Exercise pinned Codex with a local deterministic Responses fixture."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from run_native_approvals import Client, PINS, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--scenario', choices=['discovery', 'agent-execution'], default='discovery')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Use a new evidence path')
    binary = shutil.which('codex')
    if not binary or sha(binary) != PINS['codex']:
        parser.error('Codex does not match the evidence pin')
    base = Path(tempfile.mkdtemp(prefix='oda-local-model-', dir='/mnt/DATA/tmp'))
    home, workspace = base / 'home', base / 'workspace'
    home.mkdir(mode=0o700); workspace.mkdir(mode=0o700)
    requests = []
    responses = []
    root_step = 0
    child_step = 0
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
    agent = 'name = "fixture_reviewer"\ndescription = "ODA fixture agent discovery sentinel."\ndeveloper_instructions = "ODA_CHILD_CONFIGURATION_SENTINEL. Review only the isolated fixture."\n'
    canonical = base / 'source' / '.agents'
    (canonical / 'native/com.openai.codex').mkdir(parents=True)
    (canonical / 'AGENTS.md').write_text('Use the isolated fixture.\n')
    (canonical / 'manifest.json').write_text(json.dumps({'version': '1.1.0-draft.2', 'profiles': ['native']}))
    (canonical / 'native/com.openai.codex/profile.json').write_text(json.dumps({'namespace': 'com.openai.codex', 'harness_version': '=0.154.0', 'scope': 'user', 'required': True,
        'artifacts': [{'kind': 'agent', 'source': 'fixture.toml', 'name': 'fixture.toml'}]}))
    (canonical / 'native/com.openai.codex/fixture.toml').write_text(agent)
    cli = Path(__file__).resolve().parents[2] / 'CLI'
    projection = subprocess.run(['go', 'run', './cmd/agents', 'apply', '--vendor', 'codex', '--experimental', '--root', str(canonical.parent), '--scope', 'user', '--native-home', str(home), '--format', 'json'],
                                cwd=cli, capture_output=True, text=True, env={**__import__('os').environ, 'XDG_STATE_HOME': str(base / 'state')})
    for p in home.rglob('*'):
        if p.is_file(): p.chmod(0o600)
    result = {'native_version': '0.154.0', 'binary_sha256': sha(binary), 'fixture': str(base),
              'external_model': False, 'copied_credentials': False, 'full_adapter_support': False,
              'scope': 'local deterministic model transport and custom agent prompt discovery',
              'scenario': args.scenario, 'projection': {'returncode': projection.returncode, 'stdout': projection.stdout, 'stderr': projection.stderr},
              'protocol_source': 'https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/core/tests/common/responses.rs'}
    client = Client([str(Path(binary).resolve()), 'app-server', '--stdio'], workspace,
                    {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'CODEX_HOME': str(home)}, 'deny', '')
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
    result['local_response_complete'] = result.get('completed_turn', {}).get('turn', {}).get('status') == 'completed' and bool(requests)
    result['agent_discovered_in_request'] = any('ODA fixture agent discovery sentinel.' in json.dumps(r.get('body', {})) for r in requests)
    marker = workspace / 'agent-effect.txt'
    result['agent_effect'] = marker.read_text() if marker.is_file() else None
    result['child_config_in_request'] = any('ODA_CHILD_CONFIGURATION_SENTINEL' in json.dumps(r.get('body', {}).get('input', [])) for r in requests)
    result['scenario_passed'] = result['local_response_complete'] and result['agent_discovered_in_request']
    if args.scenario == 'agent-execution':
        result['scenario_passed'] = result['scenario_passed'] and result['child_config_in_request'] and result['agent_effect'] == 'native-agent-proof'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    args.output.with_suffix('.runner.py').write_bytes(Path(__file__).read_bytes())
    print(json.dumps({k: result.get(k) for k in ['scenario_passed', 'local_response_complete', 'agent_discovered_in_request', 'child_config_in_request', 'agent_effect', 'error']}))
    return 0 if result['scenario_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
