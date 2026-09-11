#!/usr/bin/env python3
"""Check Codex native account-auth selection across import/apply/reimport."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import tomllib
import traceback
import uuid

from run_native_approvals import PINS, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--auth', choices=['on', 'off'], default='on')
    parser.add_argument('--cli-root', type=Path, help='build a historical CLI checkout for a retained failure')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    assert not output.exists() and not output.with_suffix('.runner.py').exists()
    repo = Path(__file__).resolve().parents[2]
    cli_root = args.cli_root.resolve() if args.cli_root else repo / 'CLI'
    binary = Path('/home/maurizio/.local/bin/codex')
    assert sha(binary) == PINS['codex']
    root = Path(tempfile.mkdtemp(prefix='oda-provider-auth-', dir='/mnt/DATA/tmp'))
    source, target, owner, workspace = [root / n for n in ('source', 'target', 'owner', 'workspace')]
    for p in (source, target, owner, workspace): p.mkdir(mode=0o700)
    subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
    # Native account material is fixture setup, not an adapter-owned artifact.
    for home in (source, target):
        (home / 'auth.json').write_text(json.dumps({'OPENAI_API_KEY': 'oda-synthetic-model-auth'}))
        (home / 'auth.json').chmod(0o600)
    auth_before = {home.name: sha(home / 'auth.json') for home in (source, target)}
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(root / 'host-home'), 'XDG_STATE_HOME': str(root / 'state')}
    requests = []
    result = {'passed': False, 'auth': args.auth, 'fixture': str(root), 'native_version': '0.154.0',
              'native_sha256': sha(binary), 'runner_sha256': sha(__file__), 'cli_root': str(cli_root),
              'helper_sha256': sha(Path(__file__).with_name('run_native_approvals.py')),
              'implementation_sha256': {str(p.relative_to(cli_root)): sha(p) for p in sorted((cli_root / 'internal/config').glob('*.go'))},
              'commands': [], 'phases': [], 'auth_before': auth_before, 'full_adapter_support': False,
              'limitations': ['Local synthetic model and account files only.', 'No login, remote provider, or missing-credential enforcement claim.']}

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append({'path': self.path, 'body': body,
                             'authorized': self.headers.get('Authorization') == 'Bearer oda-synthetic-model-auth'})
            if not self.path.endswith('/responses'):
                self.send_response(200); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', '2'); self.end_headers(); self.wfile.write(b'{}')
                return
            rid = 'oda-model-' + str(len(requests))
            item = {'type': 'message', 'role': 'assistant', 'id': rid + '-message',
                    'content': [{'type': 'output_text', 'text': 'Fixture complete.'}]}
            events = [{'type': 'response.created', 'response': {'id': rid}},
                      {'type': 'response.output_item.done', 'item': item},
                      {'type': 'response.completed', 'response': {'id': rid, 'usage': {
                          'input_tokens': 3, 'output_tokens': 2, 'total_tokens': 5,
                          'input_tokens_details': None, 'output_tokens_details': None}}}]
            data = ''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events).encode()
            self.send_response(200); self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def command(argv, home=source, cwd=workspace, check=True):
        run = subprocess.run([str(a) for a in argv], cwd=cwd,
                             env=None if argv[0] == 'go' else dict(env, CODEX_HOME=str(home)),
                             capture_output=True, text=True, timeout=40)
        data = {'command': [str(a) for a in argv], 'exit_code': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr}
        result['commands'].append(data)
        if check: assert run.returncode == 0, json.dumps(data)
        return data

    def native(home, label):
        nonce = 'ODA_PROVIDER_AUTH_' + uuid.uuid4().hex
        start = len(requests)
        run = command([binary, 'exec', '--json', nonce], home=home, check=False)
        events = [json.loads(line) for line in run['stdout'].splitlines() if line.startswith('{')]
        phase = {'label': label, 'requests': requests[start:], 'events': events, 'exit_code': run['exit_code'], 'stderr': run['stderr']}
        result['phases'].append(phase)
        assert run['exit_code'] == 0 and any(e['type'] == 'turn.completed' for e in events), 'native turn failed'
        model_requests = [r for r in phase['requests'] if r['path'].endswith('/responses')]
        assert model_requests and all(nonce in json.dumps(r['body']) for r in model_requests)
        assert all(r['authorized'] == (args.auth == 'on') for r in model_requests), 'native account authentication changed'
        phase['correlated'] = True

    try:
        config = '''model = "fixture-model"
model_provider = "fixture"
[analytics]
enabled = false
[features]
remote_plugin = false
recommended_plugins = false
apps = false
enable_request_compression = false
[model_providers.fixture]
name = "Local fixture"
wire_api = "responses"
supports_websockets = false
'''+f'base_url = "http://127.0.0.1:{server.server_port}/v1"\nrequires_openai_auth = '+str(args.auth == 'on').lower()+'\n'
        (source / 'config.toml').write_text(config)
        (source / 'config.toml').chmod(0o600)
        native(source, 'source')
        cli = root / 'agents'
        command(['go', 'build', '-trimpath', '-buildvcs=false', '-o', cli, './cmd/agents'], cwd=cli_root)
        command([cli, 'import', '--vendor', 'codex', '--root', owner, '--experimental', '--scope', 'user', '--native-home', source])
        command([cli, 'apply', '--vendor', 'codex', '--root', owner, '--experimental', '--scope', 'user', '--native-home', target])
        native(target, 'relocated')
        again = root / 'again'
        command([cli, 'import', '--vendor', 'codex', '--root', again, '--experimental', '--scope', 'user', '--native-home', target])
        name = '.agents/native/com.openai.codex/config.toml'
        result['imported_config'] = tomllib.loads((owner / name).read_text())
        assert result['imported_config'] == tomllib.loads((again / name).read_text())
        assert result['imported_config']['model_providers']['fixture']['requires_openai_auth'] == (args.auth == 'on')
        assert not list(owner.rglob('auth.json')) and not list(again.rglob('auth.json'))
        result['auth_after'] = {home.name: sha(home / 'auth.json') for home in (source, target)}
        assert auth_before == result['auth_after']
        assert (source / 'config.toml').read_text() == config
        result['passed'] = True
    except Exception:
        result['error'] = traceback.format_exc()
    finally:
        server.shutdown(); server.server_close()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.with_suffix('.runner.py').write_bytes(Path(__file__).read_bytes())
        output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({'passed': result['passed'], 'output': str(output), 'error': result.get('error')}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
