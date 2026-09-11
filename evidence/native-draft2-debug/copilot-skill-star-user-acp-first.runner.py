#!/usr/bin/env python3
"""Inspect Copilot skill metadata in isolated discovery and model requests."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import traceback
import uuid

from run_native_approvals import Client, PINS, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope', choices=['project', 'user'], required=True)
    parser.add_argument('--cases', choices=['controls', 'malformed'], default='controls')
    parser.add_argument('--invoke', choices=['baseline', 'allowed', 'model-hidden', 'menu-hidden', 'both-hidden'])
    parser.add_argument('--grant', choices=['bash', 'shell', '*', 'Bash'], default='bash')
    parser.add_argument('--interface', choices=['acp', 'cli'], default='acp')
    parser.add_argument('--trigger', choices=['model', 'user'], default='model')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    assert not output.exists() and not output.with_suffix('.runner.py').exists()
    repo = Path(__file__).resolve().parents[2]
    binary = Path('/home/maurizio/.local/bin/copilot')
    assert sha(binary) == PINS['copilot']
    base = Path(tempfile.mkdtemp(prefix='agents-copilot-skill-metadata-', dir='/mnt/DATA/tmp'))
    home, workspace = base / 'home', base / 'workspace'
    canonical = workspace if args.scope == 'project' else base / 'canonical'
    for p in (home, workspace, canonical): p.mkdir(mode=0o700, exist_ok=True)
    subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
    root = canonical / '.agents'
    (root / 'skills').mkdir(parents=True)
    (root / 'AGENTS.md').write_text('Use isolated fixture data.\n')
    (root / 'manifest.json').write_text(json.dumps({'version': '1.1.0-draft.2', 'profiles': ['skills']}))
    cases = {'baseline': '', 'hint': 'argument-hint: "[fixture-arg]"\n', 'allowed': 'allowed-tools: [bash]\n',
             'menu-hidden': 'user-invocable: false\n', 'model-hidden': 'disable-model-invocation: true\n',
             'both-hidden': 'user-invocable: false\ndisable-model-invocation: true\n'}
    cases['allowed'] = 'allowed-tools: ['+json.dumps(args.grant)+']\n'
    if args.cases == 'malformed':
        cases = {'baseline': '', 'no-name': '', 'no-description': '', 'invalid-name': '', 'boolean-string': 'user-invocable: "false"\ndisable-model-invocation: "true"\n',
                 'unknown': 'unknown-field: true\n', 'bad-yaml': 'user-invocable: [\n', 'plain-markdown': ''}
    sources = {}
    for name, fields in cases.items():
        definition = root / 'skills' / ('fixture-'+name) / 'SKILL.md'
        definition.parent.mkdir()
        text = '---\nname: fixture-'+name+'\ndescription: AGENTS_SKILL_DESCRIPTION_'+name+'\n'+fields+'---\nAGENTS_SKILL_BODY_'+name+'\n'
        if name == 'no-name': text = text.replace('name: fixture-no-name\n', '')
        if name == 'no-description': text = text.replace('description: AGENTS_SKILL_DESCRIPTION_no-description\n', '')
        if name == 'invalid-name': text = text.replace('fixture-invalid-name', 'invalid_name')
        if name == 'plain-markdown': text = '# Plain skill\nUse this fixture.\n'
        definition.write_text(text)
        sources[name] = {'text': text, 'sha256': sha(definition)}
    (home / 'config.json').write_text(json.dumps({'trustedFolders': [str(workspace)], 'firstLaunchAt': 1234}))
    (home / 'config.json').chmod(0o600)
    state_before = sha(home / 'config.json')
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'COPILOT_HOME': str(home), 'COPILOT_CACHE_HOME': str(base / 'cache'),
           'XDG_STATE_HOME': str(base / 'state'), 'COPILOT_OFFLINE': 'true', 'COPILOT_PROVIDER_TYPE': 'openai',
           'COPILOT_PROVIDER_WIRE_API': 'completions', 'COPILOT_MODEL': 'gpt-5.4'}
    result = {'passed': False, 'scope': args.scope, 'cases': args.cases, 'fixture': str(base), 'sources': sources,
              'native_version': '1.0.83', 'native_sha256': sha(binary), 'runner_sha256': sha(__file__),
              'helper_sha256': sha(Path(__file__).with_name('run_native_approvals.py')),
              'implementation_sha256': {str(p.relative_to(repo)): sha(p) for p in sorted((repo / 'CLI/internal/config').glob('*.go'))},
              'commands': [], 'full_adapter_support': False, 'tool_permission_behavior_verified': False}
    result['invoked_skill'] = args.invoke
    result['grant'], result['interface'] = args.grant, args.interface
    result['trigger'] = args.trigger
    marker, probe = workspace / 'skill-effect.txt', workspace / 'skill-effect.py'
    probe.write_text('from pathlib import Path\nPath(__file__).with_name("skill-effect.txt").write_text("AGENTS_SKILL_EFFECT")\n')
    requests = []

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(request)
            message, finish = {'role': 'assistant', 'content': 'AGENTS_SKILL_METADATA_COMPLETE'}, 'stop'
            call = None
            if args.invoke and args.trigger == 'model' and len(requests) == 1:
                call = ('skill', {'skill': 'fixture-'+args.invoke})
            elif args.invoke and len(requests) == (1 if args.trigger == 'user' else 2):
                call = ('bash', {'command': '/usr/bin/python3 '+str(probe), 'description': 'Write isolated skill effect'})
            if call:
                message = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'skill-call-'+str(len(requests)), 'type': 'function',
                           'function': {'name': call[0], 'arguments': json.dumps(call[1])}}]}
                finish = 'tool_calls'
            body = json.dumps({'id': 'fixture-'+str(len(requests)), 'object': 'chat.completion', 'created': 1,
                               'model': request['model'], 'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
                               'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env['COPILOT_PROVIDER_BASE_URL'] = f'http://127.0.0.1:{server.server_port}/v1'
    client = None

    def command(argv, cwd=workspace):
        run = subprocess.run([str(x) for x in argv], cwd=cwd, env={**os.environ, 'XDG_STATE_HOME': env['XDG_STATE_HOME']} if argv[0] == 'go' else env,
                             capture_output=True, text=True, timeout=60)
        row = {'command': [str(x) for x in argv], 'exit_code': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr}
        result['commands'].append(row)
        assert run.returncode == 0, json.dumps(row)
        return row

    try:
        cli = base / 'agents'
        command(['go', 'build', '-trimpath', '-buildvcs=false', '-o', cli, './cmd/agents'], cwd=repo / 'CLI')
        flags = ['--vendor', 'copilot', '--root', canonical, '--experimental', '--scope', args.scope]
        if args.scope == 'user': flags += ['--native-home', home]
        result['plan'] = json.loads(command([cli, 'plan', *flags, '--format', 'json'])['stdout'])
        command([cli, 'apply', *flags])
        assert sha(home / 'config.json') == state_before
        target = root / 'skills' if args.scope == 'project' else home / 'skills'
        assert all(sha(target / ('fixture-'+name) / 'SKILL.md') == item['sha256'] for name, item in sources.items())
        result['discovery'] = json.loads(command([binary, 'skill', 'list', '--json'])['stdout'])
        nonce = 'AGENTS_SKILL_METADATA_'+uuid.uuid4().hex
        result['nonce'] = nonce
        prompt_text = '/fixture-'+args.invoke+' '+nonce if args.invoke and args.trigger == 'user' else nonce
        if args.interface == 'acp':
            client = Client([str(binary), '--acp', '--disable-builtin-mcps', '--no-auto-update', '--no-remote'], workspace, env, 'deny', str(probe))
            deadline = time.monotonic()+40
            result['initialize'] = client.response(client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {}}), deadline)
            session = client.response(client.request('session/new', {'cwd': str(workspace), 'mcpServers': []}), deadline)
            result['session'] = session
            result['prompt'] = client.response(client.request('session/prompt', {'sessionId': session['sessionId'], 'prompt': [{'type': 'text', 'text': prompt_text}]}), deadline)
            assert result['prompt']['stopReason'] == 'end_turn'
        else:
            result['cli_turn'] = command([binary, '--disable-builtin-mcps', '--no-auto-update', '--no-remote', '-p', prompt_text])
        assert requests and nonce in json.dumps(requests)
        result['catalog_descriptions'] = {name: 'AGENTS_SKILL_DESCRIPTION_'+name in json.dumps(requests[0]['messages']) for name in cases}
        result['effect'] = marker.read_text() if marker.exists() else None
        result['invoked_body_in_model'] = bool(args.invoke) and any('AGENTS_SKILL_BODY_'+args.invoke in json.dumps(r['messages']) for r in requests)
        result['native_events'] = [json.loads(line) for path in (home / 'session-state').glob('*/events.jsonl') for line in path.read_text().splitlines()]
        result['passed'] = True
    except Exception:
        result['error'] = traceback.format_exc()
    finally:
        if client:
            client.close(); result.update(events=client.events, stderr=client.errors, approvals=client.approvals)
        server.shutdown(); server.server_close()
        result['requests'] = requests
        output.parent.mkdir(parents=True, exist_ok=True)
        output.with_suffix('.runner.py').write_bytes(Path(__file__).read_bytes())
        output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({k: result.get(k) for k in ('passed', 'catalog_descriptions', 'error')}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
