#!/usr/bin/env python3
"""Check machine-local Codex settings in an isolated trusted project."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import tomllib
import traceback
import uuid

from run_native_approvals import Client, PINS, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--all-keys', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    assert not output.exists() and not output.with_suffix('.runner.py').exists()
    repo = Path(__file__).resolve().parents[2]
    binary = Path('/home/maurizio/.local/bin/codex')
    assert sha(binary) == PINS['codex']
    root = Path(tempfile.mkdtemp(prefix='oda-codex-project-scope-', dir='/mnt/DATA/tmp'))
    workspace, home = root / 'project', root / 'home'
    workspace.mkdir(mode=0o700); home.mkdir(mode=0o700)
    subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(root / 'host-home'), 'CODEX_HOME': str(home), 'XDG_STATE_HOME': str(root / 'state')}
    result = {'passed': False, 'all_keys': args.all_keys, 'fixture': str(root), 'native_version': '0.154.0', 'native_sha256': sha(binary),
              'runner_sha256': sha(__file__), 'helper_sha256': sha(Path(__file__).with_name('run_native_approvals.py')),
              'implementation_sha256': {str(p.relative_to(repo)): sha(p) for p in sorted((repo / 'CLI/internal/config').glob('*.go'))},
              'commands': [], 'phases': [], 'full_adapter_support': False}
    requests = []

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            data = json.dumps({'models': [], 'data': [], 'object': 'list'}).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append({'endpoint': self.server.label, 'path': self.path, 'body': body})
            rid = 'oda-response-' + str(len(requests))
            item = {'type': 'message', 'role': 'assistant', 'id': rid+'-message', 'content': [{'type': 'output_text', 'text': 'Fixture complete.'}]}
            events = [{'type': 'response.created', 'response': {'id': rid}}, {'type': 'response.output_item.done', 'item': item},
                      {'type': 'response.completed', 'response': {'id': rid, 'usage': {'input_tokens': 3, 'output_tokens': 2, 'total_tokens': 5}}}]
            data = ''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events).encode()
            self.send_response(200); self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)

    servers = []
    for label in ('user', 'project'):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Model); server.label = label
        servers.append(server); threading.Thread(target=server.serve_forever, daemon=True).start()
    user_url, project_url = [f'http://127.0.0.1:{s.server_port}/v1' for s in servers]
    user_config = f'''model='user-model'
model_provider='user-fixture'
[analytics]
enabled=false
[features]
remote_plugin=false
recommended_plugins=false
apps=false
enable_request_compression=false
respect_system_proxy=false
[model_providers.user-fixture]
name='User fixture'
base_url={json.dumps(user_url)}
wire_api='responses'
requires_openai_auth=false
supports_websockets=false
[projects.{json.dumps(str(workspace))}]
trust_level='trusted'
'''
    (home / 'config.toml').write_text(user_config); (home / 'config.toml').chmod(0o600)
    before_home = sha(home / 'config.toml')
    project_path = workspace / '.codex/config.toml'
    project_path.parent.mkdir()
    project_values = {'model': 'project-model', 'model_provider': 'project-fixture', 'model_providers': {
        name: {'name': 'Project override', 'base_url': project_url, 'wire_api': 'responses', 'requires_openai_auth': False}
        for name in ('user-fixture', 'project-fixture')}}
    if args.all_keys:
        project_values.update(openai_base_url=project_url, chatgpt_base_url=project_url, apps_mcp_product_sku='project-sku',
                              responses_api_metadata={'project_probe': 'project-metadata'}, notify=['/usr/bin/touch', str(root / 'project-notify')],
                              profile='project-profile', profiles={'project-profile': {'model': 'profile-model'}},
                              experimental_realtime_webrtc_call_base_url=project_url, experimental_realtime_ws_base_url=project_url,
                              otel={'environment': 'project-telemetry'}, features={'respect_system_proxy': True})
    # JSON inline objects are not TOML. Encode this bounded fixture recursively.
    def toml(value):
        if isinstance(value, dict): return '{'+', '.join(json.dumps(k)+' = '+toml(v) for k, v in value.items())+'}'
        if isinstance(value, list): return '['+', '.join(toml(v) for v in value)+']'
        return json.dumps(value)
    project_text = '\n'.join(json.dumps(k)+' = '+toml(v) for k, v in project_values.items())+'\n'
    project_path.write_text(project_text)
    result['project_values'] = project_values

    def command(argv, cwd=workspace, check=True):
        run = subprocess.run([str(a) for a in argv], cwd=cwd, env=None if argv[0] == 'go' else env, capture_output=True, text=True, timeout=40)
        row = {'command': [str(a) for a in argv], 'exit_code': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr}
        result['commands'].append(row)
        if check: assert run.returncode == 0, json.dumps(row)
        return row

    def native(label):
        start = len(requests); nonce = 'ODA_PROJECT_SCOPE_'+uuid.uuid4().hex
        client = Client([str(binary), 'app-server', '--listen', 'stdio://'], workspace, env, 'deny', str(root / 'unused'))
        phase = {'label': label, 'command': client.process.args, 'nonce': nonce}
        result['phases'].append(phase)
        try:
            deadline = time.monotonic()+30
            client.response(client.request('initialize', {'clientInfo': {'name': 'oda-project-scope', 'version': '0'}, 'capabilities': {'experimentalApi': True}}), deadline)
            client.send({'method': 'initialized'})
            phase['effective_config'] = client.response(client.request('config/read', {'cwd': str(workspace), 'includeLayers': True}), deadline)
            config = phase['effective_config']['config']
            assert config['model'] == 'project-model', 'trusted project control was not loaded'
            assert config['model_provider'] == 'user-fixture', 'project changed provider selection'
            assert config['model_providers']['user-fixture']['base_url'] == user_url
            assert 'project-fixture' not in config['model_providers']
            if args.all_keys:
                assert config['features'].get('respect_system_proxy') is False
                for key, value in project_values.items():
                    if key != 'model': assert config.get(key) != value, 'project value became effective: '+key
            thread = client.response(client.request('thread/start', {'cwd': str(workspace), 'ephemeral': True}), deadline)
            phase['thread_id'] = thread['thread']['id']
            client.response(client.request('turn/start', {'threadId': phase['thread_id'], 'input': [{'type': 'text', 'text': nonce}]}), deadline)
            while True:
                event = client.receive(deadline)
                if event.get('method') == 'turn/completed':
                    phase['turn'] = event['params']['turn']; break
            assert phase['turn']['status'] == 'completed'
            phase['model_requests'] = [r for r in requests[start:] if r['path'].endswith('/responses')]
            assert phase['model_requests'] and all(r['endpoint'] == 'user' and r['body']['model'] == 'project-model' and nonce in json.dumps(r['body']) for r in phase['model_requests'])
            assert not (root / 'project-notify').exists()
            assert sha(home / 'config.toml') == before_home
            phase['correlated'] = True
        finally:
            client.close(); phase.update(events=client.events, stderr=client.errors)

    try:
        native('direct-project')
        cli = root / 'agents'
        command(['go', 'build', '-trimpath', '-buildvcs=false', '-o', cli, './cmd/agents'], cwd=repo / 'CLI')
        command([cli, 'import', '--vendor', 'codex', '--root', workspace, '--experimental'])
        canonical = workspace / '.agents/native/com.openai.codex/config.toml'
        assert tomllib.loads(canonical.read_text()) == project_values
        profile_path = canonical.with_name('profile.json')
        profile = json.loads(profile_path.read_text())
        result['imported_profile_required'] = profile['required']
        profile['required'] = True
        profile_path.write_text(json.dumps(profile))
        project_path.unlink()  # Isolated fixture reset before adapter projection.
        def snapshot():
            return {str(p.relative_to(workspace)): (p.stat().st_mode & 0o777, sha(p) if p.is_file() else 'directory') for p in workspace.rglob('*')}
        before_required = snapshot()
        refused = command([cli, 'apply', '--vendor', 'codex', '--root', workspace, '--experimental', '--force', '--backup'], check=False)
        if refused['exit_code'] == 0:
            result['incorrectly_projected_config'] = tomllib.loads(project_path.read_text())
            native('incorrectly-projected')
            raise AssertionError('adapter projected ignored project provider settings')
        assert 'ignores project' in refused['stderr'] and not project_path.exists()
        result['required_refused_before_writes'] = snapshot() == before_required and not list(workspace.rglob('*.backup-*'))
        assert result['required_refused_before_writes']
        profile = json.loads(profile_path.read_text()); profile['required'] = False
        profile_path.write_text(json.dumps(profile))
        result['optional_plan'] = json.loads(command([cli, 'plan', '--vendor', 'codex', '--root', workspace, '--experimental', '--format', 'json'])['stdout'])
        command([cli, 'apply', '--vendor', 'codex', '--root', workspace, '--experimental'])
        assert tomllib.loads(project_path.read_text()) == {'model': 'project-model'}
        assert tomllib.loads(canonical.read_text()) == project_values
        native('optional-projection')
        result['user_config_unchanged'] = sha(home / 'config.toml') == before_home
        result['canonical_preserved'] = tomllib.loads(canonical.read_text()) == project_values
        assert result['user_config_unchanged'] and result['canonical_preserved']
        result['passed'] = True
    except Exception:
        result['error'] = traceback.format_exc()
    finally:
        for server in servers: server.shutdown(); server.server_close()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.with_suffix('.runner.py').write_bytes(Path(__file__).read_bytes())
        output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({'passed': result['passed'], 'output': str(output), 'error': result.get('error')}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
