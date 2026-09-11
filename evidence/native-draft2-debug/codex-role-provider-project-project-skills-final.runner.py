#!/usr/bin/env python3
"""Check bounded Codex role overrides with isolated local model endpoints."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
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
    parser.add_argument('--scope', choices=['project', 'user'], required=True)
    parser.add_argument('--case', choices=['provider', 'shell', 'skills', 'selector'], default='provider')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    assert not output.exists() and not output.with_suffix('.runner.py').exists()
    repo = Path(__file__).resolve().parents[2]
    binary = Path('/home/maurizio/.local/bin/codex')
    assert sha(binary) == PINS['codex']
    base = Path(tempfile.mkdtemp(prefix='agents-role-scope-', dir='/mnt/DATA/tmp'))
    workspace, home = base / 'project', base / 'home'
    workspace.mkdir(mode=0o700); home.mkdir(mode=0o700)
    source = workspace if args.scope == 'project' else base / 'source'
    source.mkdir(mode=0o700, exist_ok=True)
    subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(base / 'host-home'), 'CODEX_HOME': str(home), 'XDG_STATE_HOME': str(base / 'state')}
    result = {'passed': False, 'scope': args.scope, 'case': args.case, 'fixture': str(base), 'native_version': '0.154.0',
              'native_sha256': sha(binary), 'runner_sha256': sha(__file__),
              'helper_sha256': sha(Path(__file__).with_name('run_native_approvals.py')),
              'implementation_sha256': {str(p.relative_to(repo)): sha(p) for p in sorted((repo / 'CLI/internal/config').glob('*.go'))},
              'phases': [], 'commands': [], 'full_adapter_support': False}
    requests = []
    step = 0
    nonce = ''

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            data = b'{"models": [], "data": []}'
            self.send_response(200); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
        def do_POST(self):
            nonlocal step
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            child = 'AGENTS_CHILD_CONFIGURATION_SENTINEL' in json.dumps(body.get('input', []))
            requests.append({'path': self.path, 'body': body, 'child': child})
            rid = 'agents-response-' + str(len(requests))
            item = {'type': 'message', 'role': 'assistant', 'id': rid + '-message',
                    'content': [{'type': 'output_text', 'text': nonce + ' complete.'}]}
            call = None
            if not child and step == 0:
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
    user_config = '''model='parent-model'
model_provider='parent'
approval_policy='never'
sandbox_mode='read-only'
[analytics]
enabled=false
[features]
remote_plugin=false
recommended_plugins=false
apps=false
enable_request_compression=false
respect_system_proxy=false
'''
    for provider in ('parent', 'child'):
        user_config += f'''[model_providers.{provider}]
name={json.dumps(provider)}
base_url="http://127.0.0.1:{server.server_port}/{provider}/v1"
wire_api='responses'
requires_openai_auth=false
supports_websockets=false
'''
    user_config += f'[projects.{json.dumps(str(workspace))}]\ntrust_level="trusted"\n'
    control_key = {'provider': 'model_provider', 'shell': 'features.shell_tool', 'skills': 'skills.include_instructions', 'selector': 'skills.config'}[args.case]
    control_false, control_true = control_key+'=false\n', control_key+'=true\n'
    if args.case == 'selector':
        control_false = 'skills.config=[{path='+json.dumps(str(home / 'skills/fixture/SKILL.md'))+',enabled=false}]\n'
        control_true = control_false.replace('enabled=false', 'enabled=true')
    if args.case == 'shell':
        user_config = user_config.replace('[features]\n', '[features]\nshell_tool=false\n')
    elif args.case in ('skills', 'selector'):
        user_config = control_false + user_config
    if args.case in ('skills', 'selector'):
        skill = home / 'skills/fixture/SKILL.md'
        skill.parent.mkdir(parents=True)
        skill.write_text('---\nname: fixture\ndescription: AGENTS_ROLE_SKILL_DESCRIPTION\n---\nUse the isolated fixture.\n')
    (home / 'config.toml').write_text(user_config); (home / 'config.toml').chmod(0o600)
    user_hash = sha(home / 'config.toml')
    role = '''name='fixture_reviewer'
description='Local role scope fixture.'
developer_instructions='AGENTS_CHILD_CONFIGURATION_SENTINEL'
model='child-model'
model_reasoning_effort='low'
'''
    ignored_role = role + ("model_provider='child'\n" if args.case == 'provider' else control_true)
    if args.case != 'provider': role += control_false
    target = (workspace / '.codex' if args.scope == 'project' else home) / 'agents/fixture.toml'
    target.parent.mkdir(parents=True); target.write_text(ignored_role); target.chmod(0o600)

    def native(label):
        nonlocal step, nonce
        step = 0; nonce = 'AGENTS_ROLE_' + uuid.uuid4().hex
        start = len(requests)
        client = Client([str(binary), 'app-server', '--listen', 'stdio://'], workspace, env, 'deny', str(base / 'unused'))
        phase = {'label': label, 'nonce': nonce, 'command': client.process.args}
        result['phases'].append(phase)
        try:
            deadline = time.monotonic() + 30
            client.response(client.request('initialize', {'clientInfo': {'name': 'agents-role-scope', 'version': '0'}}), deadline)
            client.send({'method': 'initialized'})
            thread = client.response(client.request('thread/start', {'cwd': str(workspace), 'ephemeral': True}), deadline)
            phase['parent_id'] = thread['thread']['id']
            client.response(client.request('turn/start', {'threadId': phase['parent_id'], 'input': [{'type': 'text', 'text': nonce}]}), deadline)
            while True:
                event = client.receive(deadline)
                if event.get('method') == 'turn/completed' and event['params']['threadId'] == phase['parent_id']:
                    phase['turn'] = event['params']['turn']; break
            assert phase['turn']['status'] == 'completed'
            phase['requests'] = requests[start:]
            children = [r for r in phase['requests'] if r['child']]
            assert children, 'no child model request'
            assert all(r['path'] == '/parent/v1/responses' and r['body']['model'] == 'child-model'
                       and r['body'].get('reasoning', {}).get('effort') == 'low' and nonce in json.dumps(r['body']) for r in children)
            if args.case != 'provider':
                def enabled(request):
                    if args.case == 'shell': return any(t.get('name') == 'exec_command' for t in request['body'].get('tools', []))
                    return 'AGENTS_ROLE_SKILL_DESCRIPTION' in json.dumps(request['body'].get('input', []))
                parents = [r for r in phase['requests'] if not r['child']]
                assert parents and all(enabled(r) == (label == 'supported-role-projected') for r in parents), 'parent control failed'
                assert all(not enabled(r) for r in children), 'role capability increase applied or reduction failed'
                phase['parent_control_enabled'] = enabled(parents[0])
                phase['child_control_enabled'] = enabled(children[0])
            completed = [e['params'] for e in client.events if e.get('method') == 'item/completed']
            spawned = {tid for p in completed if p.get('item', {}).get('tool') == 'spawnAgent'
                       and p['item'].get('status') == 'completed' for tid in p['item'].get('receiverThreadIds', [])}
            assert spawned and any(e.get('method') == 'turn/completed' and e['params'].get('threadId') in spawned
                                   and e['params']['turn']['status'] == 'completed' for e in client.events), 'no correlated child completion'
            phase['correlated'] = True
        finally:
            client.close(); phase.update(events=client.events, stderr=client.errors)

    def command(argv, cwd=workspace, check=True):
        run = subprocess.run([str(a) for a in argv], cwd=cwd, env={**os.environ, 'XDG_STATE_HOME': env['XDG_STATE_HOME']} if argv[0] == 'go' else env,
                             capture_output=True, text=True, timeout=60)
        row = {'command': [str(a) for a in argv], 'exit_code': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr}
        result['commands'].append(row)
        if check: assert run.returncode == 0, json.dumps(row)
        return row

    try:
        native('direct-role-provider-ignored')
        cli = base / 'agents'
        command(['go', 'build', '-trimpath', '-buildvcs=false', '-o', cli, './cmd/agents'], cwd=repo / 'CLI')
        flags = ['--vendor', 'codex', '--root', source, '--experimental', '--scope', args.scope]
        if args.scope == 'user': flags += ['--native-home', home]
        command([cli, 'import', *flags])
        canonical = source / '.agents/native/com.openai.codex/agent/fixture.toml'
        canonical_role = canonical.read_text()
        expected_role, imported_role = tomllib.loads(ignored_role), tomllib.loads(canonical_role)
        if args.case == 'selector':
            for parsed in (expected_role, imported_role):
                for selector in parsed['skills']['config']:
                    selector['path'] = str((target.parent / selector['path']).resolve())
        assert imported_role == expected_role, 'import changed the role or its referenced skill'
        manifest_path = source / '.agents/manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['profiles'] = ['native']
        manifest_path.write_text(json.dumps(manifest))
        profile_path = canonical.parent.parent / 'profile.json'
        profile = json.loads(profile_path.read_text())
        # Limit this probe to the standalone artifact. Parent providers stay external.
        profile['artifacts'] = [a for a in profile['artifacts'] if a['kind'] == 'agent']
        profile['required'] = True; profile_path.write_text(json.dumps(profile))
        target.unlink()
        def snapshot():
            return {str(p.relative_to(base)): [p.stat().st_mode & 0o777, sha(p) if p.is_file() else 'directory']
                    for folder in (workspace, source, home, base / 'state') if folder.exists() for p in folder.rglob('*')}
        before = snapshot()
        refused = command([cli, 'apply', *flags, '--force', '--backup'], check=False)
        if refused['exit_code'] == 0:
            result['incorrectly_projected_role'] = target.read_text()
            native('incorrectly-projected-role')
            raise AssertionError('adapter activated an ignored role provider')
        assert 'native agent' in refused['stderr'] and control_key in refused['stderr']
        result['required_refused_before_writes'] = snapshot() == before
        assert result['required_refused_before_writes']
        profile['required'] = False; profile_path.write_text(json.dumps(profile))
        result['optional_plan'] = json.loads(command([cli, 'plan', *flags, '--format', 'json'])['stdout'])
        command([cli, 'apply', *flags])
        assert not target.exists() and canonical.read_text() == canonical_role
        assert sha(home / 'config.toml') == user_hash
        result['optional_preserved_inactive'] = True
        canonical.write_text(role)
        profile['required'] = True; profile_path.write_text(json.dumps(profile))
        if args.case != 'provider':
            if args.case == 'shell':
                user_config = user_config.replace('shell_tool=false\n', 'shell_tool=true\n')
            else:
                user_config = user_config.replace(control_false, control_true)
            (home / 'config.toml').write_text(user_config)
            user_hash = sha(home / 'config.toml')
        command([cli, 'apply', *flags])
        assert target.read_text() == role
        native('supported-role-projected')
        result['user_config_unchanged'] = sha(home / 'config.toml') == user_hash
        assert result['user_config_unchanged']
        result['passed'] = True
    except Exception:
        result['error'] = traceback.format_exc()
    finally:
        server.shutdown(); server.server_close()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.with_suffix('.runner.py').write_bytes(Path(__file__).read_bytes())
        output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'passed': result['passed'], 'output': str(output), 'error': result.get('error')}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
