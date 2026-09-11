#!/usr/bin/env python3
"""Check Codex agent configuration references after native-home relocation."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
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
    parser.add_argument('--reference', choices=['external', 'managed', 'absolute', 'declared-only'], required=True)
    parser.add_argument('--scope', choices=['project', 'user'], default='user')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    assert not output.exists() and not output.with_suffix('.runner.py').exists()
    repo = Path(__file__).resolve().parents[2]
    binary = Path('/home/maurizio/.local/bin/codex')
    assert sha(binary) == PINS['codex']
    base = Path(tempfile.mkdtemp(prefix='agents-role-reference-', dir='/mnt/DATA/tmp'))
    source, target, workspace, canonical = [base / name for name in ('source', 'target', 'workspace', 'canonical')]
    for path in (source, target, workspace, canonical): path.mkdir(mode=0o700)
    for path in (source, target, workspace): subprocess.run(['git', 'init', '-q', str(path)], check=True)
    config_dir = source if args.scope == 'user' else source / '.codex'
    target_config_dir = target if args.scope == 'user' else target / '.codex'
    if args.scope == 'project': canonical = source
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(base / 'host-home'), 'XDG_STATE_HOME': str(base / 'state')}
    result = {'passed': False, 'reference': args.reference, 'scope': args.scope, 'fixture': str(base), 'native_version': '0.154.0',
              'native_sha256': sha(binary), 'runner_sha256': sha(__file__),
              'helper_sha256': sha(Path(__file__).with_name('run_native_approvals.py')),
              'implementation_sha256': {str(p.relative_to(repo)): sha(p) for p in sorted((repo / 'CLI/internal/config').glob('*.go'))},
              'commands': [], 'phases': [], 'full_adapter_support': False}
    requests = []
    step, nonce = 0, ''

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            data = b'{"models": [], "data": []}'
            self.send_response(200); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
        def do_POST(self):
            nonlocal step
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            child = 'AGENTS_REFERENCED_ROLE_INSTRUCTIONS' in json.dumps(body.get('input', []))
            requests.append({'path': self.path, 'body': body, 'child': child})
            rid = 'agents-reference-response-' + str(len(requests))
            item = {'type': 'message', 'role': 'assistant', 'id': rid+'-message',
                    'content': [{'type': 'output_text', 'text': nonce+' complete.'}]}
            call = None
            if not child and step == 0 and 'AGENTS_REFERENCED_ROLE_DESCRIPTION' in json.dumps(body):
                step = 1
                call = {'name': 'spawn_agent', 'arguments': {'agent_type': 'fixture_reviewer', 'message': nonce}}
            elif not child and step == 1:
                for entry in body.get('input', []):
                    if entry.get('type') != 'function_call_output': continue
                    try: reply = json.loads(entry.get('output', ''))
                    except (ValueError, TypeError): continue
                    if reply.get('agent_id'):
                        step = 2
                        call = {'name': 'wait_agent', 'arguments': {'targets': [reply['agent_id']], 'timeout_ms': 10000}}
                        break
            if call:
                item = {'type': 'function_call', 'namespace': 'multi_agent_v1', 'call_id': rid,
                        'name': call['name'], 'arguments': json.dumps(call['arguments'])}
            events = [{'type': 'response.created', 'response': {'id': rid}},
                      {'type': 'response.output_item.done', 'item': item},
                      {'type': 'response.completed', 'response': {'id': rid, 'usage': {'input_tokens': 3, 'output_tokens': 2, 'total_tokens': 5}}}]
            data = ''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events).encode()
            self.send_response(200); self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    role = config_dir / ('agents' if args.reference in ('managed', 'declared-only') else 'role-library') / 'fixture.toml'
    role.parent.mkdir(parents=True)
    role.write_text('''name='fixture_reviewer'
description='AGENTS_REFERENCED_ROLE_DESCRIPTION'
developer_instructions='AGENTS_REFERENCED_ROLE_INSTRUCTIONS'
model='child-model'
model_reasoning_effort='low'
''')
    role.chmod(0o600)
    if args.reference == 'declared-only':
        role.write_text(role.read_text().replace("name='fixture_reviewer'\n", '').replace("description='AGENTS_REFERENCED_ROLE_DESCRIPTION'\n", ''))
    original_role_hash = sha(role)
    reference = str(role) if args.reference == 'absolute' else str(role.relative_to(config_dir))
    config = f'''model='parent-model'
model_provider='fixture'
[analytics]
enabled=false
[features]
remote_plugin=false
recommended_plugins=false
apps=false
respect_system_proxy=false
enable_request_compression=false
[model_providers.fixture]
name='Local fixture'
base_url='http://127.0.0.1:{server.server_port}/v1'
wire_api='responses'
requires_openai_auth=false
supports_websockets=false
[agents.fixture_reviewer]
description='AGENTS_REFERENCED_ROLE_DESCRIPTION'
config_file={json.dumps(reference)}
'''
    native_home = base / 'project-home'
    if args.scope == 'project':
        native_home.mkdir(mode=0o700)
        user_config, declaration = config.split('[agents.fixture_reviewer]', 1)
        for path in (source, target): user_config += '[projects.'+json.dumps(str(path))+']\ntrust_level="trusted"\n'
        (native_home / 'config.toml').write_text(user_config); (native_home / 'config.toml').chmod(0o600)
        config = '[agents.fixture_reviewer]'+declaration
    (config_dir / 'config.toml').write_text(config); (config_dir / 'config.toml').chmod(0o600)
    source_config_hash = sha(config_dir / 'config.toml')
    result['source_reference'] = reference

    def native(label, home):
        nonlocal step, nonce
        step, nonce = 0, 'AGENTS_REFERENCE_'+uuid.uuid4().hex
        start = len(requests)
        cwd = workspace if args.scope == 'user' else home
        client = Client([str(binary), 'app-server', '--listen', 'stdio://'], cwd,
                        {**env, 'CODEX_HOME': str(home if args.scope == 'user' else native_home)}, 'deny', '')
        phase = {'label': label, 'command': client.process.args, 'nonce': nonce}
        result['phases'].append(phase)
        try:
            deadline = time.monotonic()+30
            client.response(client.request('initialize', {'clientInfo': {'name': 'agents-role-reference', 'version': '0'}}), deadline)
            client.send({'method': 'initialized'})
            thread = client.response(client.request('thread/start', {'cwd': str(cwd), 'ephemeral': True}), deadline)
            phase['parent_id'] = thread['thread']['id']
            client.response(client.request('turn/start', {'threadId': phase['parent_id'], 'input': [{'type': 'text', 'text': nonce}]}), deadline)
            while True:
                event = client.receive(deadline)
                if event.get('method') == 'turn/completed' and event['params']['threadId'] == phase['parent_id']:
                    phase['turn'] = event['params']['turn']; break
            phase['requests'] = requests[start:]
            assert phase['turn']['status'] == 'completed'
            children = [r for r in phase['requests'] if r['child']]
            assert children, 'referenced role did not produce a child model request after '+label
            assert all(r['body']['model'] == 'child-model' and r['body']['reasoning']['effort'] == 'low'
                       and nonce in json.dumps(r['body']) for r in children)
            completed = [e['params'] for e in client.events if e.get('method') == 'item/completed']
            spawned = {tid for p in completed if p.get('item', {}).get('tool') == 'spawnAgent'
                       and p['item'].get('status') == 'completed' for tid in p['item'].get('receiverThreadIds', [])}
            assert spawned and any(e.get('method') == 'turn/completed' and e['params'].get('threadId') in spawned
                                   and e['params']['turn']['status'] == 'completed' for e in client.events)
            phase['correlated'] = True
        finally:
            client.close(); phase.update(events=client.events, stderr=client.errors)

    def command(argv, cwd=workspace):
        run = subprocess.run([str(a) for a in argv], cwd=cwd,
                             env={**os.environ, 'XDG_STATE_HOME': env['XDG_STATE_HOME']} if argv[0] == 'go' else env,
                             capture_output=True, text=True, timeout=60)
        row = {'command': [str(a) for a in argv], 'exit_code': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr}
        result['commands'].append(row)
        assert run.returncode == 0, json.dumps(row)
        return row

    try:
        native('source', source)
        cli = base / 'agents'
        command(['go', 'build', '-trimpath', '-buildvcs=false', '-o', cli, './cmd/agents'], cwd=repo / 'CLI')
        flags = ['--vendor', 'codex', '--experimental', '--scope', args.scope]
        command([cli, 'import', *flags, '--root', canonical, *(['--native-home', source] if args.scope == 'user' else [])])
        imported_path = canonical / '.agents/native/com.openai.codex/config.toml'
        result['imported_config'] = tomllib.loads(imported_path.read_text())
        if args.scope == 'project':
            shutil.copytree(canonical / '.agents', target / '.agents')
        command([cli, 'apply', *flags, '--root', canonical if args.scope == 'user' else target,
                 *(['--native-home', target] if args.scope == 'user' else [])])
        result['projected_config'] = tomllib.loads((target_config_dir / 'config.toml').read_text())
        native('relocated', target)
        expected = 'agents/fixture.toml' if args.reference == 'managed' else str(role)
        assert result['projected_config']['agents']['fixture_reviewer']['config_file'] == expected
        assert result['imported_config']['agents']['fixture_reviewer']['config_file'] == expected
        again = base / 'again'
        if args.scope == 'project': shutil.copytree(target_config_dir, again / '.codex')
        command([cli, 'import', *flags, '--root', again, *(['--native-home', target] if args.scope == 'user' else [])])
        result['reimported_config'] = tomllib.loads((again / '.agents/native/com.openai.codex/config.toml').read_text())
        assert result['reimported_config'] == result['imported_config']
        assert sha(role) == original_role_hash and sha(config_dir / 'config.toml') == source_config_hash
        assert not (target_config_dir / 'role-library').exists()
        if args.reference == 'managed': assert sha(target_config_dir / 'agents/fixture.toml') == original_role_hash
        result['source_unchanged'] = True
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
