#!/usr/bin/env python3
"""Exercise Codex provider token commands before and after native projection."""
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

from run_native_approvals import Client, PINS, sha, native_binary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=['success', 'timeout', 'empty', 'exit', 'invalid-utf8', 'missing', 'cache', 'refresh', 'retry'], default='success')
    parser.add_argument('--cli-root', type=Path)
    parser.add_argument('--observe-failure-fallback', action='store_true', help='record the native unauthenticated fallback limitation; do not claim authentication enforcement')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.observe_failure_fallback and args.case in ('success', 'cache', 'refresh', 'retry'):
        parser.error('fallback observation requires a token-command failure case')
    output = args.output.resolve()
    assert not output.exists() and not output.with_suffix('.runner.py').exists() and not output.with_suffix('.token.py').exists()
    repo = Path(__file__).resolve().parents[2]
    cli_root = args.cli_root.resolve() if args.cli_root else repo / 'CLI'
    binary = native_binary('codex')
    assert sha(binary) == PINS['codex']
    root = Path(tempfile.mkdtemp(prefix='oda-command-auth-'))
    source, target, owner, workspace, command_dir = [root / n for n in ('source', 'target', 'owner', 'workspace', 'command')]
    for p in (source, target, owner, workspace, command_dir): p.mkdir(mode=0o700)
    subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
    calls_file = command_dir / 'calls.jsonl'
    helper = command_dir / 'token.py'
    helper.write_text('''import json, os, pathlib, sys, time
p = pathlib.Path('calls.jsonl')
with p.open('a') as f: f.write(json.dumps({'cwd': os.getcwd(), 'args': sys.argv[1:]})+'\\n')
mode = sys.argv[1]
if mode == 'timeout': time.sleep(2)
if mode == 'exit': sys.exit(7)
if mode == 'empty': sys.exit(0)
if mode == 'invalid-utf8': sys.stdout.buffer.write(b'\\xff'); sys.exit(0)
print('oda-command-synthetic-token-'+str(len(p.read_text().splitlines())))
''')
    helper.chmod(0o600)
    helper_before = sha(helper)
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(root / 'host-home'), 'XDG_STATE_HOME': str(root / 'state')}
    requests = []
    phase_request_count = 0
    result = {'passed': False, 'case': args.case, 'fixture': str(root), 'native_version': '0.154.0',
              'native_sha256': sha(binary), 'runner_sha256': sha(__file__), 'cli_root': str(cli_root),
              'helper_sha256': sha(Path(__file__).with_name('run_native_approvals.py')),
              'implementation_sha256': {str(p.relative_to(cli_root)): sha(p) for p in sorted((cli_root / 'internal/config').glob('*.go'))},
              'commands': [], 'phases': [], 'token_helper_sha256': helper_before, 'full_adapter_support': False,
              'observe_failure_fallback': args.observe_failure_fallback, 'authentication_enforcement_verified': False}

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            data = json.dumps({'models': [], 'data': [], 'object': 'list'}).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
        def do_POST(self):
            nonlocal phase_request_count
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            phase_request_count += 1
            revision = len(calls())
            rejected = args.case == 'retry' and phase_request_count == 1
            requests.append({'path': self.path, 'body': body, 'authorized': self.headers.get('Authorization') == 'Bearer oda-command-synthetic-token-'+str(revision),
                             'token_revision': revision, 'status': 401 if rejected else 200})
            if rejected:
                data = json.dumps({'error': {'message': 'fixture expired token', 'type': 'invalid_api_key'}}).encode()
                self.send_response(401); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
                return
            rid = 'oda-response-' + str(len(requests))
            item = {'type': 'message', 'role': 'assistant', 'id': rid + '-message', 'content': [{'type': 'output_text', 'text': 'Fixture complete.'}]}
            events = [{'type': 'response.created', 'response': {'id': rid}}, {'type': 'response.output_item.done', 'item': item},
                      {'type': 'response.completed', 'response': {'id': rid, 'usage': {'input_tokens': 3, 'output_tokens': 2, 'total_tokens': 5}}}]
            data = ''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events).encode()
            self.send_response(200); self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def calls():
        return [json.loads(line) for line in calls_file.read_text().splitlines()] if calls_file.exists() else []

    def command(argv, home=source, cwd=workspace, check=True):
        run = subprocess.run([str(a) for a in argv], cwd=cwd, env=None if argv[0] == 'go' else dict(env, CODEX_HOME=str(home)), capture_output=True, text=True, timeout=35)
        row = {'command': [str(a) for a in argv], 'exit_code': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr}
        result['commands'].append(row)
        if check: assert run.returncode == 0, json.dumps(row)
        return row

    def native(home, label):
        nonlocal phase_request_count
        phase_request_count = 0
        nonce = 'ODA_COMMAND_AUTH_' + uuid.uuid4().hex
        start, call_start = len(requests), len(calls())
        started = time.monotonic()
        if args.case in ('cache', 'refresh'):
            client = Client([str(binary), 'app-server', '--listen', 'stdio://'], workspace, dict(env, CODEX_HOME=str(home)), 'deny', str(root / 'unused'))
            phase = {'label': label, 'command': client.process.args, 'turns': [], 'calls_after_turn': []}
            result['phases'].append(phase)
            try:
                deadline = time.monotonic()+30
                client.response(client.request('initialize', {'clientInfo': {'name': 'oda-command-auth', 'version': '0'}, 'capabilities': {'experimentalApi': True}}), deadline)
                client.send({'method': 'initialized'})
                thread = client.response(client.request('thread/start', {'cwd': str(workspace), 'ephemeral': True}), deadline)
                phase['thread_id'] = thread['thread']['id']
                for index in range(2):
                    if index: time.sleep(0.15)
                    client.response(client.request('turn/start', {'threadId': phase['thread_id'], 'input': [{'type': 'text', 'text': nonce+'_'+str(index)}]}), deadline)
                    while True:
                        event = client.receive(deadline)
                        if event.get('method') == 'turn/completed':
                            assert event['params']['threadId'] == phase['thread_id']
                            phase['turns'].append(event['params']['turn'])
                            break
                    assert phase['turns'][-1]['status'] == 'completed'
                    phase['calls_after_turn'].append(len(calls())-call_start)
                a, b = phase['calls_after_turn']
                assert a >= 1 and (b == a if args.case == 'cache' else b > a), 'token refresh interval was not observed'
                phase['native_alive_before_close'] = client.process.poll() is None
            finally:
                client.close()
                phase.update(events=client.events, stderr=client.errors, calls=calls()[call_start:], requests=requests[start:])
            rows = [r for r in phase['requests'] if r['path'].endswith('/responses')]
            assert len(rows) >= 2 and all(r['authorized'] and nonce in json.dumps(r['body']) for r in rows)
            assert phase['calls'] and all(c == {'cwd': str(command_dir), 'args': [args.case, 'literal;argument', '$(literal)']} for c in phase['calls'])
            phase['correlated'] = True
            return
        run = command([binary, 'exec', '--json', nonce], home=home, check=False)
        events = [json.loads(line) for line in run['stdout'].splitlines() if line.startswith('{')]
        phase = {'label': label, 'requests': requests[start:], 'events': events, 'exit_code': run['exit_code'], 'stderr': run['stderr'],
                 'elapsed_seconds': time.monotonic()-started, 'calls': calls()[call_start:]}
        result['phases'].append(phase)
        rows = [r for r in phase['requests'] if r['path'].endswith('/responses')]
        if args.case in ('success', 'retry'):
            assert run['exit_code'] == 0 and any(e['type'] == 'turn.completed' for e in events), 'native turn failed'
            assert rows and all(r['authorized'] and nonce in json.dumps(r['body']) for r in rows), 'authenticated request missing'
            if args.case == 'retry':
                assert len(rows) >= 2 and rows[0]['status'] == 401 and rows[-1]['status'] == 200
                assert rows[-1]['token_revision'] > rows[0]['token_revision'] and len(phase['calls']) >= 2
        else:
            expected = {'timeout': 'timed out', 'empty': 'empty token', 'exit': 'exited with status', 'invalid-utf8': 'non-UTF-8', 'missing': 'failed to start'}[args.case]
            assert expected in run['stderr'] + run['stdout'], 'native failure reason missing'
            if args.observe_failure_fallback:
                assert run['exit_code'] == 0 and any(e['type'] == 'turn.completed' for e in events)
                assert rows and all(not r['authorized'] and nonce in json.dumps(r['body']) for r in rows)
                phase['native_unauthenticated_fallback'] = True
            else:
                assert run['exit_code'] != 0 and not rows, 'failed token command did not prevent model requests'
        if args.case == 'missing': assert not phase['calls']
        else:
            assert phase['calls'] and all(c == {'cwd': str(command_dir), 'args': [args.case, 'literal;argument', '$(literal)']} for c in phase['calls'])
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
'''+f'base_url = "http://127.0.0.1:{server.server_port}/v1"\n[model_providers.fixture.auth]\ncommand = '+json.dumps('/missing/oda-token-command' if args.case == 'missing' else '/usr/bin/python3')+'\nargs = '+json.dumps(['token.py', args.case, 'literal;argument', '$(literal)'])+'\ncwd = '+json.dumps(str(command_dir))+'\ntimeout_ms = '+('100' if args.case == 'timeout' else '1000')+'\nrefresh_interval_ms = '+('50' if args.case == 'refresh' else '0')+'\n'
        (source / 'config.toml').write_text(config); (source / 'config.toml').chmod(0o600)
        native(source, 'source')
        count = len(calls())
        cli = root / 'agents'
        command(['go', 'build', '-trimpath', '-buildvcs=false', '-o', cli, './cmd/agents'], cwd=cli_root)
        command([cli, 'import', '--vendor', 'codex', '--root', owner, '--experimental', '--scope', 'user', '--native-home', source])
        command([cli, 'apply', '--vendor', 'codex', '--root', owner, '--experimental', '--scope', 'user', '--native-home', target])
        assert len(calls()) == count, 'adapter executed the token command'
        result['adapter_did_not_execute'] = True
        native(target, 'relocated')
        again = root / 'again'
        count = len(calls())
        command([cli, 'import', '--vendor', 'codex', '--root', again, '--experimental', '--scope', 'user', '--native-home', target])
        name = '.agents/native/com.openai.codex/config.toml'
        result['imported_config'] = tomllib.loads((owner / name).read_text())
        assert result['imported_config'] == tomllib.loads((again / name).read_text()) == tomllib.loads(config)
        assert len(calls()) == count
        result['external_helper_unchanged'] = sha(helper) == helper_before
        result['no_helper_copied'] = not list(owner.rglob('token.py')) and not list(again.rglob('token.py'))
        result['no_auth_files'] = not list(root.rglob('auth.json'))
        result['source_config_unchanged'] = (source / 'config.toml').read_text() == config
        assert all(result[k] for k in ('external_helper_unchanged', 'no_helper_copied', 'no_auth_files', 'source_config_unchanged'))
        result['passed'] = True
    except Exception:
        result['error'] = traceback.format_exc()
    finally:
        server.shutdown(); server.server_close()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.with_suffix('.runner.py').write_bytes(Path(__file__).read_bytes())
        output.with_suffix('.token.py').write_bytes(helper.read_bytes())
        output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({'passed': result['passed'], 'output': str(output), 'error': result.get('error')}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
