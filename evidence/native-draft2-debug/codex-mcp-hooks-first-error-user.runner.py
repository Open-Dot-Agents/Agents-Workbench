#!/usr/bin/env python3
"""Test projected Codex MCP hooks with isolated native trust and local servers.

The MCP connection and execution policy are explicit native fixture setup.
Only hook files or inline hook settings pass through the adapter in this test.
No real credentials, external model, or user stores are used.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import tomllib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from run_native_approvals import Client, PINS, sha
from run_native_codex_hooks import toml


MCP = r'''
import json, pathlib, sys, time
log = pathlib.Path(sys.argv[1])
def record(value):
    with log.open('a') as stream:
        stream.write(json.dumps(dict(value, recorded_at=time.monotonic())) + '\n')
for line in sys.stdin:
    request = json.loads(line)
    record(request)
    method, identifier = request.get('method'), request.get('id')
    if identifier is None:
        continue
    result = {}
    if method == 'initialize':
        result = {'protocolVersion': request['params']['protocolVersion'],
                  'capabilities': {'tools': {}},
                  'serverInfo': {'name': 'oda-hook-fixture', 'version': '1'}}
    elif method == 'tools/list':
        result = {'tools': [{'name': 'record', 'description': 'Local hook fixture.',
                            'inputSchema': {'type': 'object', 'additionalProperties': True}}]}
    elif method == 'tools/call':
        arguments = request['params'].get('arguments', {})
        scenario = arguments.get('scenario')
        if scenario == 'timeout':
            time.sleep(5)
        response = {'hookEventName': arguments.get('event', 'PreToolUse'),
                    'additionalContext': 'ODA_MCP_HOOK_' + arguments.get('event', '')}
        if scenario == 'deny':
            response.update(permissionDecision='deny', permissionDecisionReason='ODA_MCP_HOOK_DENIED')
        output = {'hookSpecificOutput': response}
        result = {'content': [{'type': 'text', 'text': json.dumps(output)}]}
        if scenario == 'error':
            result = {'isError': True, 'content': [{'type': 'text', 'text': 'ODA_MCP_HOOK_ERROR'}]}
        record({'event': 'result', 'request_id': identifier, 'result': result})
    print(json.dumps({'jsonrpc': '2.0', 'id': identifier, 'result': result}), flush=True)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope', choices=['project', 'user'], required=True)
    parser.add_argument('--location', choices=['file', 'inline'], default='file')
    parser.add_argument('--scenario', choices=['execution', 'deny', 'missing-server',
                        'missing-tool', 'error', 'timeout', 'unreviewed', 'template-missing', 'numbers'],
                        default='execution')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    snapshot = output.with_suffix('.runner.py')
    if output.exists() or snapshot.exists():
        raise SystemExit('Refuse to replace evidence')
    binary = Path(shutil.which('codex'))
    assert sha(binary) == PINS['codex'], 'native binary pin mismatch'
    repo = Path(__file__).resolve().parents[2]
    root = Path(tempfile.mkdtemp(prefix='oda-codex-mcp-hooks-', dir='/mnt/DATA/tmp'))
    home, workspace, source = [root / name for name in ['home', 'workspace', 'source']]
    for path in [home, workspace, source]:
        path.mkdir(mode=0o700)
    for path in [workspace, source]:
        subprocess.run(['git', 'init', '-q', str(path)], check=True)
    mcp = root / 'mcp.py'
    mcp.write_text(MCP)
    log = root / 'mcp.jsonl'
    probe, effect = workspace / 'probe.py', workspace / 'effect.txt'
    probe.write_text('from pathlib import Path\nPath(' + repr(str(effect)) +
                     ').write_text("ODA_CODEX_TOOL_EFFECT")\n')

    def group(event, matcher='Bash'):
        inputs = {'event': '${hook_event_name}', 'session': '${session_id}',
                  'call': '${tool_use_id}', 'payload': '${tool_input}',
                  'text': 'command=${tool_input.command}',
                  'nested': [{'name': '${tool_name}'}, 7, True],
                  'scenario': args.scenario if event == 'PreToolUse' else 'execution'}
        if args.scenario == 'numbers':
            inputs['numbers'] = [9223372036854775807, 9223372036854775808, 18446744073709551615, -9223372036854775808]
        if args.scenario == 'template-missing' and event == 'PreToolUse':
            inputs['absent'] = '${oda_missing_field}'
        return {'matcher': matcher, 'hooks': [{
            'type': 'mcp_tool',
            'server': 'missing' if args.scenario == 'missing-server' and event == 'PreToolUse' else 'oda_hook',
            'tool': 'missing' if args.scenario == 'missing-tool' and event == 'PreToolUse' else 'record',
            'input': inputs, 'timeout': 1 if args.scenario == 'timeout' else 3,
            'statusMessage': 'ODA_MCP_LOADING_' + event}]}

    config = {'hooks': {'PreToolUse': [group('PreToolUse'), group('PreToolUse', 'apply_patch')],
                        'PostToolUse': [group('PostToolUse')]}}
    inline = '[hooks]\n' + '\n'.join(json.dumps(k) + ' = ' + toml(v)
                                     for k, v in config['hooks'].items()) + '\n'
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            requests.append(body)
            rid = 'fixture-response-' + str(len(requests))
            item = {'type': 'message', 'role': 'assistant', 'id': 'fixture-message',
                    'content': [{'type': 'output_text', 'text': 'Fixture complete.'}]}
            if len(requests) == 1:
                item = {'type': 'function_call', 'call_id': 'fixture-hook-command',
                        'name': 'exec_command', 'arguments': json.dumps({
                            'cmd': f'/usr/bin/python3 {probe}', 'workdir': str(workspace)})}
            events = [{'type': 'response.created', 'response': {'id': rid}},
                      {'type': 'response.output_item.done', 'item': item},
                      {'type': 'response.completed', 'response': {'id': rid, 'usage': {
                          'input_tokens': 0, 'input_tokens_details': None, 'output_tokens': 0,
                          'output_tokens_details': None, 'total_tokens': 0}}}]
            data = ''.join('event: ' + e['type'] + '\ndata: ' + json.dumps(e) + '\n\n'
                           for e in events).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    native_config = ('model="fixture-model"\nmodel_provider="fixture"\n'
                     'approval_policy="never"\nsandbox_mode="workspace-write"\n'
                     '[features]\nenable_request_compression=false\nhooks=true\n'
                     '[model_providers.fixture]\nname="Local fixture"\n'
                     f'base_url="http://127.0.0.1:{server.server_port}/v1"\n'
                     'wire_api="responses"\nrequires_openai_auth=false\nsupports_websockets=false\n'
                     '[projects.' + json.dumps(str(workspace)) + ']\ntrust_level="trusted"\n'
                     '[mcp_servers.oda_hook]\ncommand="/usr/bin/python3"\nargs=' +
                     toml([str(mcp), str(log)]) + '\nstartup_timeout_sec=10\ntool_timeout_sec=10\n')
    (home / 'config.toml').write_text(native_config)
    before = {str(f.relative_to(home)): sha(f) for f in home.rglob('*') if f.is_file()}
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'CODEX_HOME': str(home),
           'XDG_STATE_HOME': str(root / 'state')}
    cli = root / 'agents'
    subprocess.run(['go', 'build', '-o', str(cli), './cmd/agents'], cwd=repo / 'CLI', check=True)
    result = {'fixture': str(root), 'scope': args.scope, 'location': args.location,
              'scenario': args.scenario, 'native_version': '0.154.0', 'native_sha256': sha(binary),
              'runner_sha256': sha(__file__), 'cli_sha256': sha(cli), 'configuration': config,
              'external_model': False, 'copied_credentials': False, 'full_adapter_support': False,
              'mcp_configuration': 'isolated native prerequisite; not projected in this fixture',
              'helper_sha256': {name: sha(Path(__file__).with_name(name)) for name in
                                ['run_native_approvals.py', 'run_native_codex_hooks.py']},
              'implementation_sha256': {str(f.relative_to(repo)): sha(f)
                  for f in sorted((repo / 'CLI/internal/config').glob('native*.go'))}}

    def target(base):
        return base / ('.codex' if args.scope == 'project' else '') / (
            'config.toml' if args.location == 'inline' else 'hooks.json')

    def run_cli(operation, canonical, native_home=None):
        command = [str(cli), operation, '--vendor', 'codex', '--root', str(canonical),
                   '--scope', args.scope, '--experimental']
        if native_home:
            command += ['--native-home', str(native_home)]
        completed = subprocess.run(command, env=env, capture_output=True, text=True)
        return {'command': command, 'exit': completed.returncode,
                'stdout': completed.stdout, 'stderr': completed.stderr}

    command = [str(binary), 'app-server', '--stdio']
    result['native_command'] = command
    client = None
    try:
        native_target = target(workspace if args.scope == 'project' else home)
        seed = target(source)
        seed.parent.mkdir(parents=True, exist_ok=True)
        seed.write_text(inline if args.location == 'inline' else json.dumps(config))
        result['import'] = run_cli('import', source, source if args.scope == 'user' else None)
        assert result['import']['exit'] == 0, 'import failed'
        shutil.copytree(source / '.agents', workspace / '.agents')
        result['apply'] = run_cli('apply', workspace, home if args.scope == 'user' else None)
        assert result['apply']['exit'] == 0, 'apply failed'
        after = {str(f.relative_to(home)): sha(f) for f in home.rglob('*') if f.is_file()}
        result.update(user_files_before_apply=before, user_files_after_apply=after)
        if args.scope == 'project':
            assert before == after, 'project apply changed user configuration'
        if args.location == 'file':
            assert (home / 'config.toml').read_text() == native_config, 'apply changed native prerequisites'
            assert json.loads(native_target.read_text()) == config, 'projection changed hook values'

        def connect():
            connection = Client(command, workspace, env, 'deny', str(probe))
            deadline = time.monotonic() + 30
            connection.response(connection.request('initialize', {
                'clientInfo': {'name': 'oda-mcp-hooks', 'version': '0.1.0'},
                'capabilities': {'experimentalApi': True}}), deadline)
            connection.send({'method': 'initialized', 'params': {}})
            return connection

        client = connect()
        listing = client.response(client.request('hooks/list', {'cwds': [str(workspace)]}),
                                  time.monotonic() + 30)
        result['hooks_list'] = listing
        definitions = [h for entry in listing['data'] for h in entry['hooks']]
        assert len(definitions) == 3, 'unexpected hook count'
        assert all(h['sourcePath'] == str(native_target) and not h['isManaged']
                   for h in definitions), 'unexpected hook source'
        # Trust only the exact isolated source after byte/value and native-source checks.
        assert all(h['trustStatus'] == 'untrusted' for h in definitions), 'unexpected initial trust'
        client.close()
        result['inspection_events'] = client.events
        if args.scenario != 'unreviewed':
            with (home / 'config.toml').open('a') as store:
                for definition in definitions:
                    store.write('\n[hooks.state.' + json.dumps(definition['key']) + ']\ntrusted_hash=' +
                                json.dumps(definition['currentHash']) + '\nenabled=true\n')
        trust_hash = sha(home / 'config.toml')
        result['fixture_native_config_hash'] = trust_hash
        result['reapply'] = run_cli('apply', workspace, home if args.scope == 'user' else None)
        assert result['reapply']['exit'] == 0, 'reapply failed'
        assert sha(home / 'config.toml') == trust_hash, 'reapply changed native trust or prerequisites'

        reimport = root / 'reimport'
        if args.scope == 'project':
            reimport.mkdir()
            subprocess.run(['git', 'init', '-q', str(reimport)], check=True)
            shutil.copytree(workspace / '.codex', reimport / '.codex')
        result['reimport'] = run_cli('import', reimport, home if args.scope == 'user' else None)
        assert result['reimport']['exit'] == 0, 'second import failed'
        namespace = reimport / '.agents/native/com.openai.codex'
        profile = json.loads((namespace / 'profile.json').read_text())
        artifact = next(a for a in profile['artifacts']
                        if a['kind'] == ('config' if args.location == 'inline' else 'hooks'))
        imported = (tomllib.loads if args.location == 'inline' else json.loads)(
            (namespace / artifact['source']).read_text())
        assert imported['hooks'] == config['hooks'], 'second import changed hook values or imported trust'

        client = connect()
        deadline = time.monotonic() + 45
        result['execution_hooks_list'] = client.response(client.request(
            'hooks/list', {'cwds': [str(workspace)]}), deadline)
        expected = 'untrusted' if args.scenario == 'unreviewed' else 'trusted'
        assert all(h['trustStatus'] == expected for e in result['execution_hooks_list']['data']
                   for h in e['hooks']), 'native hook trust mismatch'
        thread = client.response(client.request('thread/start', {
            'cwd': str(workspace), 'ephemeral': True}), deadline)
        result['thread'] = thread
        client.response(client.request('turn/start', {'threadId': thread['thread']['id'],
            'input': [{'type': 'text', 'text': 'Run the isolated fixture command.'}]}), deadline)
        while True:
            event = client.receive(deadline)
            if event.get('method') == 'turn/completed':
                result['completed_turn'] = event['params']['turn']
                break
        events = [json.loads(line) for line in log.read_text().splitlines()]
        calls = [e for e in events if e.get('method') == 'tools/call']
        result['mcp_events'] = events
        if args.scenario == 'unreviewed':
            assert not calls, 'unreviewed hook called MCP'
        else:
            expected_count = 1 if args.scenario in ['deny', 'missing-server', 'missing-tool', 'template-missing'] else 2
            assert len(calls) == expected_count, 'unexpected MCP call count'
            for call in calls:
                values = call['params']['arguments']
                assert call['params']['name'] == 'record', 'unexpected MCP tool'
                assert values['session'] == thread['thread']['id'], 'hook session mismatch'
                assert values['call'] == 'fixture-hook-command', 'hook tool-call mismatch'
                assert isinstance(values['payload'], dict) and str(probe) in values['payload']['command'], 'object template mismatch'
                assert values['text'] == 'command=' + values['payload']['command'], 'embedded template mismatch'
                if args.scenario == 'numbers':
                    assert values['numbers'] == [9223372036854775807, 9223372036854775808, 18446744073709551615, -9223372036854775808], 'native numeric input changed'
                assert values['nested'] == [{'name': 'Bash'}, 7, True], 'recursive template mismatch'
            if args.scenario in ['execution', 'deny', 'error', 'timeout']:
                assert calls[0]['params']['arguments']['event'] == 'PreToolUse', 'pre-hook absent'
            if args.scenario not in ['deny', 'timeout']:
                assert calls[-1]['params']['arguments']['event'] == 'PostToolUse', 'post-hook absent'
                assert 'ODA_MCP_HOOK_PostToolUse' in json.dumps(requests[-1]['input']), 'post-hook context absent'
        if args.scenario == 'deny':
            assert not effect.exists(), 'denied command executed'
            assert 'ODA_MCP_HOOK_DENIED' in json.dumps(requests[-1]['input']), 'denial not returned'
        else:
            assert effect.read_text() == 'ODA_CODEX_TOOL_EFFECT', 'command effect absent'
            assert any(e.get('method') == 'item/completed' and
                       e.get('params', {}).get('item', {}).get('type') == 'commandExecution' and
                       e['params']['item'].get('status') == 'completed' and
                       str(probe) in e['params']['item'].get('command', '') for e in client.events), 'no correlated command completion'
        assert not client.approvals, 'unexpected native approval request under fixture policy'
        assert len(requests) == 2, 'unexpected model invocation count'
        result['passed'] = True
    except Exception as error:
        result.update(passed=False, error=str(error))
    finally:
        if client:
            client.close()
            result.update(events=client.events, approvals=client.approvals, stderr=client.errors)
        server.shutdown()
        server.server_close()
        result.update(model_requests=requests, effect=effect.read_text() if effect.exists() else '')
        if log.exists():
            result['mcp_events'] = [json.loads(line) for line in log.read_text().splitlines()]
        snapshot.write_bytes(Path(__file__).read_bytes())
        output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'output': str(output), 'passed': result['passed'], 'error': result.get('error')}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
