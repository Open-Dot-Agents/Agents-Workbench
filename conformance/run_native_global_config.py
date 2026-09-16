#!/usr/bin/env python3
"""Measure global core loading, shared skill discovery, and project defaults."""
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
    parser.add_argument('--vendor', choices=['codex', 'copilot'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    vendor, output = args.vendor, args.output.resolve()
    snapshot = output.with_suffix('.runner.py')
    assert not output.exists() and not snapshot.exists(), 'refuse evidence replacement'
    binary = native_binary(vendor)
    assert sha(binary) == PINS[vendor], 'native pin mismatch'
    with snapshot.open('xb') as stream: stream.write(Path(__file__).read_bytes())
    repo = Path(__file__).resolve().parents[2]
    base = Path(tempfile.mkdtemp(prefix='agents-global-'))
    home, project = base/'home', base/'project'
    native = home/('.'+vendor)
    native.mkdir(parents=True, mode=0o700)
    project.mkdir(mode=0o700)
    subprocess.run(['git', 'init', '-q', str(project)], check=True)
    cli = base/'agents'
    env = {'HOME': str(home), 'PATH': '/usr/bin:/bin', 'XDG_STATE_HOME': str(base/'state')}
    result = {'vendor': vendor, 'fixture': str(base), 'native_version': '0.154.0' if vendor == 'codex' else '1.0.84-9',
              'native_sha256': sha(binary), 'runner_sha256': sha(snapshot),
              'helper_sha256': sha(Path(__file__).with_name('run_native_approvals.py')),
              'implementation_sha256': {str(p.relative_to(repo)): sha(p) for directory in ('CLI/internal/config', 'CLI/cmd/agents') for p in sorted((repo/directory).glob('*.go'))},
              'commands': [], 'phases': [], 'full_adapter_support': False,
              'limitations': ['Native instruction loading does not establish model compliance.',
                              'Codex model precedence is tested; other fields keep their native scope restrictions.',
                              'Copilot BYOK requires COPILOT_MODEL; its model precedence is not tested.',
                              'Shared skills are discovered without duplication; this run does not execute their scripts.']}
    requests, errors = [], []

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            try:
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append(request)
                identity = 'fixture-'+str(len(requests))
                if vendor == 'codex':
                    item = {'type': 'message', 'role': 'assistant', 'id': identity+'-message',
                            'content': [{'type': 'output_text', 'text': 'AGENTS_GLOBAL_NATIVE_DONE'}]}
                    events = [{'type': 'response.created', 'response': {'id': identity}}, {'type': 'response.output_item.done', 'item': item},
                              {'type': 'response.completed', 'response': {'id': identity, 'usage': {'input_tokens': 3, 'output_tokens': 2, 'total_tokens': 5}}}]
                    body = ''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events).encode()
                    content_type = 'text/event-stream'
                else:
                    assert request['stream'], 'expected streaming native request'
                    chunks = [{'id': identity, 'object': 'chat.completion.chunk', 'created': 1, 'model': request['model'],
                               'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': 'AGENTS_GLOBAL_NATIVE_DONE'}, 'finish_reason': None}]},
                              {'id': identity, 'object': 'chat.completion.chunk', 'created': 1, 'model': request['model'],
                               'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}],
                               'usage': {'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5}}]
                    body = (''.join('data: '+json.dumps(chunk)+'\n\n' for chunk in chunks)+'data: [DONE]\n\n').encode()
                    content_type = 'text/event-stream'
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as error: errors.append(str(error))

    server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_port}/v1'
    if vendor == 'codex':
        env['CODEX_HOME'] = str(native)
        authority = native/'config.toml'
        authority.write_text(f'model_provider="fixture"\n[analytics]\nenabled=false\n[features]\nremote_plugin=false\nrecommended_plugins=false\napps=false\nenable_request_compression=false\nrespect_system_proxy=false\n[model_providers.fixture]\nname="Fixture"\nbase_url={json.dumps(url)}\nwire_api="responses"\nrequires_openai_auth=false\nsupports_websockets=false\n[projects.{json.dumps(str(project))}]\ntrust_level="trusted"\n')
    else:
        env.update(COPILOT_HOME=str(native), COPILOT_CACHE_HOME=str(base/'cache'), COPILOT_OFFLINE='true',
                   COPILOT_PROVIDER_TYPE='openai', COPILOT_PROVIDER_WIRE_API='completions', COPILOT_PROVIDER_BASE_URL=url, COPILOT_MODEL='gpt-5.4')
        authority = native/'config.json'
        authority.write_text(json.dumps({'trustedFolders': [str(project)], 'firstLaunchAt': 1234}))
    authority.chmod(0o600)
    result['environment'] = env

    def external_authority():
        if vendor == 'copilot': return sha(authority)
        value = tomllib.loads(authority.read_text())
        value.pop('model', None)
        return value

    def invoke(argv, cwd=project):
        before = external_authority()
        run = subprocess.run([str(x) for x in argv], cwd=cwd, env=None if argv[0] == 'go' else env, capture_output=True, text=True, timeout=90)
        row = {'command': [str(x) for x in argv], 'exit_code': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr}
        if str(argv[0]) == str(cli):
            row.update(authority_before=before, authority_after=external_authority())
            assert row['authority_before'] == row['authority_after'], 'adapter changed external authority'
        result['commands'].append(row)
        assert run.returncode == 0, json.dumps(row)
        return row

    def session(label):
        phase = {'label': label, 'nonce': 'AGENTS_GLOBAL_'+uuid.uuid4().hex}
        result['phases'].append(phase)
        offset = len(requests)
        command = [str(binary), 'app-server', '--listen', 'stdio://'] if vendor == 'codex' else [str(binary), '--acp', '--disable-builtin-mcps', '--no-auto-update', '--no-remote']
        client = Client(command, project, env, 'deny', str(base/'unused'))
        try:
            deadline = time.monotonic()+45
            if vendor == 'codex':
                client.response(client.request('initialize', {'clientInfo': {'name': 'oda-global', 'version': '0'}, 'capabilities': {'experimentalApi': True}}), deadline)
                client.send({'method': 'initialized'})
                phase['effective_config'] = client.response(client.request('config/read', {'cwd': str(project), 'includeLayers': True}), deadline)
                phase['discovery'] = client.response(client.request('skills/list', {'cwds': [str(project)], 'forceReload': True}), deadline)
                thread = client.response(client.request('thread/start', {'cwd': str(project), 'ephemeral': True}), deadline)
                phase['session_id'] = thread['thread']['id']
                client.response(client.request('turn/start', {'threadId': phase['session_id'], 'input': [{'type': 'text', 'text': phase['nonce']}]}), deadline)
                while True:
                    event = client.receive(deadline)
                    if event.get('method') == 'turn/completed': phase['completion'] = event; break
                assert phase['completion']['params']['turn']['status'] == 'completed'
                model = 'project-model' if label == 'project' else 'global-model'
                assert phase['effective_config']['config']['model'] == model
            else:
                phase['discovery'] = json.loads(invoke([binary, 'skill', 'list', '--json'])['stdout'])
                client.response(client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {}}), deadline)
                session_info = client.response(client.request('session/new', {'cwd': str(project), 'mcpServers': []}), deadline)
                phase['session_id'] = session_info['sessionId']
                phase['completion'] = client.response(client.request('session/prompt', {'sessionId': phase['session_id'], 'prompt': [{'type': 'text', 'text': phase['nonce']}]}), deadline)
                assert phase['completion']['stopReason'] == 'end_turn'
                model = 'gpt-5.4'
            phase['model_requests'] = requests[offset:]
            assert phase['model_requests'] and all(r['model'] == model and phase['nonce'] in json.dumps(r) for r in phase['model_requests'])
            context = json.dumps(phase['model_requests'])
            assert 'AGENTS_GLOBAL_CORE' in context
            assert ('AGENTS_PROJECT_CORE' in context) == (label != 'global')
            assert 'global-shared-fixture' in json.dumps(phase['discovery'])
            assert str(home/'.agents/skills/global-shared-fixture') in json.dumps(phase['discovery'])
            assert not (native/'skills/global-shared-fixture').exists()
            assert any('AGENTS_GLOBAL_NATIVE_DONE' in json.dumps(e) and phase['session_id'] in json.dumps(e) for e in client.events), 'native response was not observed'
            assert not errors, errors
        finally:
            client.close()
            phase.update(events=client.events, approvals=client.approvals, stderr=client.errors)

    try:
        invoke(['go', 'build', '-o', cli, './cmd/agents'], cwd=repo/'CLI')
        result['cli_sha256'] = sha(cli)
        invoke([cli, 'init', '--global', '--experimental'])
        canonical = home/'.agents'
        (canonical/'AGENTS.md').write_text('AGENTS_GLOBAL_CORE\n')
        (canonical/'manifest.json').write_text(json.dumps({'version': '1.1.0-draft.2', 'profiles': ['native', 'skills']}))
        skill = canonical/'skills/global-shared-fixture/SKILL.md'
        skill.parent.mkdir(parents=True)
        skill.write_text('---\nname: global-shared-fixture\ndescription: Shared user fixture.\n---\nUse the isolated fixture.\n')
        namespace = 'com.openai.codex' if vendor == 'codex' else 'com.github.copilot'
        profile_path = canonical/'native'/namespace/'profile.json'
        profile = json.loads(profile_path.read_text())
        config_name = 'config.toml' if vendor == 'codex' else 'settings.json'
        profile['artifacts'].append({'kind': 'config', 'source': config_name})
        profile_path.write_text(json.dumps(profile))
        (profile_path.parent/config_name).write_text('model="global-model"\n' if vendor == 'codex' else '{"model":"gpt-5.4","memory":false}')
        invoke([cli, 'validate', '--global', '--experimental'])
        result['global_plan'] = json.loads(invoke([cli, 'plan', '--global', '--experimental', '--vendor', vendor, '--native-home', native, '--format', 'json'])['stdout'])
        invoke([cli, 'apply', '--global', '--experimental', '--vendor', vendor, '--native-home', native])
        result['user_before_project'] = {str(p.relative_to(native)): sha(p) for p in native.iterdir() if p.name in ('AGENTS.md', 'copilot-instructions.md', 'config.toml', 'settings.json', 'config.json')}
        session('global')
        local = project/'.agents'
        local.mkdir()
        (local/'AGENTS.md').write_text('AGENTS_PROJECT_CORE\n')
        (local/'manifest.json').write_text('{"version":"1.1.0-draft.2","profiles":["native"]}')
        directory = local/'native'/namespace
        directory.mkdir(parents=True)
        (directory/'profile.json').write_text(json.dumps({'namespace': namespace, 'harness_version': '='+result['native_version'], 'scope': 'project', 'required': True, 'artifacts': [{'kind': 'config', 'source': config_name}]}))
        (directory/config_name).write_text('model="project-model"\n' if vendor == 'codex' else '{}')
        result['project_plan'] = json.loads(invoke([cli, 'apply', '--root', project, '--experimental', '--vendor', vendor, '--format', 'json'])['stdout'])
        assert result['project_plan']['native']['global_source'] == str(canonical)
        session('project')
        (directory/config_name).write_text('' if vendor == 'codex' else '{}')
        invoke([cli, 'apply', '--root', project, '--experimental', '--vendor', vendor])
        session('fallback')
        result['user_after_project'] = {name: sha(native/name) for name in result['user_before_project']}
        # Copilot can update its own runtime config. Record adapter write
        # isolation separately through the config ownership and Go tests.
        immutable = [name for name in result['user_before_project'] if name != 'config.json']
        assert all(result['user_before_project'][name] == result['user_after_project'][name] for name in immutable)
        result['passed'] = True
    except Exception as error:
        result.update(passed=False, error=str(error), traceback=traceback.format_exc())
    finally:
        server.shutdown()
        server.server_close()
        result['provider_errors'] = errors
        result['native_logs'] = {str(p.relative_to(base)): p.read_text() for p in sorted((native/'logs').glob('*.log'))}
        with output.open('x') as stream: json.dump(result, stream, indent=2); stream.write('\n')
    print(json.dumps({'passed': result['passed'], 'output': str(output), 'error': result.get('error')}))
    return 0 if result['passed'] else 1


if __name__ == '__main__': raise SystemExit(main())
