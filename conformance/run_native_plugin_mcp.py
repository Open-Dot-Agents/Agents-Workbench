#!/usr/bin/env python3
"""Execute a shared Agent Plugins stdio MCP fixture through each pinned client.

The model endpoint is deterministic and local. Native trust and approval policy
are fixture prerequisites. No portable approval mapping is claimed.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import traceback

from run_native_approvals import Client, PINS, sha
from run_native_codex_hooks import toml


MCP = r'''
import json, os, pathlib, sys, time
log = pathlib.Path(sys.argv[1])
def record(value):
    with log.open('a') as stream: stream.write(json.dumps(dict(value, recorded_at=time.monotonic())) + '\n')
record({'event': 'process', 'args': sys.argv, 'cwd': os.getcwd(), 'env': {
    key: os.getenv(key) for key in ['PLUGIN_ROOT','PLUGIN_DATA','ODA_ROOT','ODA_DATA','ODA_UNKNOWN']}})
for line in sys.stdin:
    request = json.loads(line)
    record(request)
    method, identifier = request.get('method'), request.get('id')
    if identifier is None: continue
    result = {}
    if method == 'initialize':
        result = {'protocolVersion': request['params']['protocolVersion'], 'capabilities': {'tools': {}},
                  'serverInfo': {'name': 'oda-portable-mcp', 'version': '1'}}
    if method == 'tools/list':
        result = {'tools': [{'name': 'portable_record', 'description': 'Write the isolated portable MCP marker.',
            'inputSchema': {'type': 'object', 'properties': {'marker': {'type': 'string'}}, 'required': ['marker']}}]}
    if method == 'tools/call':
        assert request['params']['name'] == 'portable_record'
        assert request['params']['arguments'] == {'marker': 'ODA_PORTABLE_CALL'}
        (log.parent / 'effect.txt').write_text('ODA_PORTABLE_EFFECT')
        result = {'content': [{'type': 'text', 'text': 'ODA_PORTABLE_RESULT'}]}
        record({'event': 'result', 'request_id': identifier, 'result': result})
    print(json.dumps({'jsonrpc': '2.0', 'id': identifier, 'result': result}), flush=True)
'''


class PluginClient(Client):
    def send(self, message):
        # The generic fixture client refuses unknown server requests. Handle
        # only the exact MCP approval request for this generated fixture.
        event = self.events[-1] if self.events else {}
        params = event.get('params', {})
        meta = params.get('_meta', {})
        if ('error' in message and event.get('id') == message.get('id') and
                event.get('method') == 'mcpServer/elicitation/request' and
                params.get('serverName') == 'fixture-mcp' and params.get('mode') == 'form' and
                meta.get('codex_approval_kind') == 'mcp_tool_call' and
                meta.get('tool_params') == {'marker': 'ODA_PORTABLE_CALL'} and
                '"portable_record"' in params.get('message', '')):
            approved = self.decision == 'plugin-allow'
            response = {'action': 'accept' if approved else 'decline', 'content': {}}
            self.approvals.append({'method': event['method'], 'params': params, 'response': response, 'approved': approved, 'responded_at': time.monotonic()})
            return super().send({'id': event['id'], 'result': response})
        tool = params.get('toolCall', {})
        if ('result' in message and event.get('id') == message.get('id') and
                event.get('method') == 'session/request_permission' and
                tool.get('toolCallId') == 'fixture-portable-call' and
                tool.get('rawInput') == {'marker': 'ODA_PORTABLE_CALL'}):
            approved = self.decision == 'plugin-allow'
            kind = 'allow_once' if approved else 'reject_once'
            option = next((o for o in params['options'] if o['kind'] == kind), None)
            response = {'outcome': {'outcome': 'selected', 'optionId': option['optionId']}} if option else {'outcome': {'outcome': 'cancelled'}}
            if self.approvals and self.approvals[-1]['params'] == params: self.approvals.pop()
            self.approvals.append({'method': event['method'], 'params': params, 'response': response, 'approved': approved, 'responded_at': time.monotonic()})
            return super().send({'id': event['id'], 'result': response})
        return super().send(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vendor', choices=['codex', 'copilot'], required=True)
    parser.add_argument('--scope', choices=['project', 'user'], required=True)
    parser.add_argument('--native-approval', choices=['never', 'prompt', 'approve'], default='approve')
    parser.add_argument('--decision', choices=['allow', 'deny'], default='allow')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.vendor == 'copilot' and args.native_approval == 'never': parser.error('never is a Codex-only native policy in this fixture')
    if args.decision == 'deny' and args.native_approval != 'prompt': parser.error('denial requires prompt mode')
    output = args.output.resolve()
    snapshot = output.with_suffix('.runner.py')
    assert not output.exists() and not snapshot.exists(), 'refuse evidence replacement'
    binary = Path(shutil.which(args.vendor))
    assert sha(binary) == PINS[args.vendor], 'native pin mismatch'
    repo = Path(__file__).resolve().parents[2]
    root = Path(tempfile.mkdtemp(prefix='oda-plugin-mcp-'))
    home, workspace, market = [root / name for name in ['home', 'workspace', 'market']]
    for path in [home, workspace, market]: path.mkdir(mode=0o700)
    subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
    plugin = market / 'fixture'
    plugin.mkdir()
    (plugin / 'plugin.json').write_text(json.dumps({'$schema': 'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json',
                                                  'name': 'fixture', 'version': '1.0.0'}))
    (plugin / 'mcp.json').write_text(json.dumps({'$schema': 'https://agent-plugins.org/schemas/1.0.0/mcp.schema.json',
        'mcpServers': {'fixture-mcp': {'type': 'stdio', 'command': 'python3',
            'args': ['${PLUGIN_ROOT}/server.py', '${PLUGIN_DATA}/oda-portable-mcp-events.jsonl'], 'cwd': '${PLUGIN_DATA}',
            'env': {'ODA_ROOT': '${PLUGIN_ROOT}', 'ODA_DATA': '${PLUGIN_DATA}', 'ODA_UNKNOWN': '${ODA_AMBIENT}'}}}}))
    (plugin / 'server.py').write_text(MCP)
    package_hashes = {str(p.relative_to(plugin)): sha(p) for p in plugin.rglob('*') if p.is_file()}
    catalog = {'name': 'oda-mcp-fixture', 'owner': {'name': 'Fixture'}, 'plugins': [{'name': 'fixture', 'source': './fixture'}]}
    for path in [market / 'marketplace.json', market / '.agents/plugins/marketplace.json']:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(catalog))
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'CODEX_HOME': str(home), 'COPILOT_HOME': str(home),
           'COPILOT_CACHE_HOME': str(root / 'cache'), 'XDG_STATE_HOME': str(root / 'state'),
           'COPILOT_OFFLINE': 'true', 'COPILOT_PROVIDER_TYPE': 'openai', 'COPILOT_PROVIDER_WIRE_API': 'completions',
           'COPILOT_MODEL': 'fixture-model', 'ODA_AMBIENT': 'MUST_NOT_EXPAND'}
    result = {'vendor': args.vendor, 'scope': args.scope, 'native_approval': args.native_approval, 'decision': args.decision, 'fixture': str(root), 'native_sha256': sha(binary),
              'native_version': '0.154.0' if args.vendor == 'codex' else '1.0.84-9', 'runner_sha256': sha(__file__),
              'implementation_sha256': {str(p.relative_to(repo)): sha(p) for p in sorted((repo / 'CLI/internal/config').glob('*.go'))},
              'helper_sha256': {name: sha(Path(__file__).with_name(name)) for name in ['run_native_approvals.py', 'run_native_codex_hooks.py']},
              'commands': [], 'model_requests': [], 'sessions': [], 'package_hashes': package_hashes,
              'external_model': False, 'copied_credentials': False, 'full_adapter_support': False}
    phase = 'enabled'

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_): pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            previous = [r for r in result['model_requests'] if r['phase'] == phase]
            result['model_requests'].append({'phase': phase, 'request': request})
            matches = []
            for tool in request.get('tools', []):
                if tool.get('type') == 'namespace':
                    matches.extend((child['name'], tool['name']) for child in tool.get('tools', []) if child.get('name') == 'portable_record')
                else:
                    name = tool.get('function', tool).get('name', '')
                    if name.endswith('portable_record'): matches.append((name, None))
            call = phase == 'enabled' and not previous and len(matches) == 1
            message = {'role': 'assistant', 'content': 'Fixture complete.'}
            item = {'type': 'message', 'role': 'assistant', 'id': 'fixture-message',
                    'content': [{'type': 'output_text', 'text': 'Fixture complete.'}]}
            if call:
                arguments = json.dumps({'marker': 'ODA_PORTABLE_CALL'})
                message = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'fixture-portable-call',
                    'type': 'function', 'function': {'name': matches[0][0], 'arguments': arguments}}]}
                item = {'type': 'function_call', 'call_id': 'fixture-portable-call', 'name': matches[0][0], 'arguments': arguments}
                if matches[0][1]: item['namespace'] = matches[0][1]
            if args.vendor == 'copilot':
                body = {'id': 'fixture', 'object': 'chat.completion', 'created': 1, 'model': 'fixture-model',
                        'choices': [{'index': 0, 'message': message, 'finish_reason': 'tool_calls' if call else 'stop'}],
                        'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}
                data, content_type = json.dumps(body).encode(), 'application/json'
            else:
                rid = 'response-' + str(len(result['model_requests']))
                events = [{'type': 'response.created', 'response': {'id': rid}},
                          {'type': 'response.output_item.done', 'item': item},
                          {'type': 'response.completed', 'response': {'id': rid, 'usage': {'input_tokens': 0,
                           'input_tokens_details': None, 'output_tokens': 0, 'output_tokens_details': None, 'total_tokens': 0}}}]
                data = ''.join('event: ' + e['type'] + '\ndata: ' + json.dumps(e) + '\n\n' for e in events).encode()
                content_type = 'text/event-stream'
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    http = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    env['COPILOT_PROVIDER_BASE_URL'] = f'http://127.0.0.1:{http.server_port}/v1'
    filename = 'config.toml' if args.vendor == 'codex' else 'settings.json'
    namespace = 'com.openai.codex' if args.vendor == 'codex' else 'com.github.copilot'
    cli = root / 'agents'

    def invoke(command, cwd=workspace):
        run = subprocess.run(command, cwd=cwd, env=None if command[0] == 'go' else env,
                             capture_output=True, text=True, timeout=45)
        record = {'command': [str(p) for p in command], 'exit_code': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr}
        result['commands'].append(record)
        assert run.returncode == 0, json.dumps(record)
        return record

    def apply():
        command = [str(cli), 'apply', '--vendor', args.vendor, '--root', str(workspace), '--scope', args.scope, '--experimental']
        if args.scope == 'user': command += ['--native-home', str(home)]
        invoke(command)

    def encode(value):
        return json.dumps(value) if args.vendor == 'copilot' else '\n'.join(json.dumps(k) + ' = ' + toml(v) for k, v in value.items()) + '\n'

    def session():
        command = [str(binary), 'app-server', '--stdio'] if args.vendor == 'codex' else [str(binary), '--acp',
            '--disable-builtin-mcps', '--no-auto-update', '--no-remote']
        if args.vendor == 'copilot' and args.native_approval == 'approve': command += ['--allow-tool', 'fixture-mcp(portable_record)']
        client = PluginClient(command, workspace, env, 'plugin-' + args.decision, '')
        try:
            deadline = time.monotonic() + 35
            if args.vendor == 'copilot':
                client.response(client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {}}), deadline)
                started = client.response(client.request('session/new', {'cwd': str(workspace), 'mcpServers': []}), deadline)
                client.response(client.request('session/prompt', {'sessionId': started['sessionId'], 'prompt': [
                    {'type': 'text', 'text': 'Use the isolated portable MCP fixture.'}]}), deadline)
            else:
                client.response(client.request('initialize', {'clientInfo': {'name': 'oda-plugin-mcp', 'version': '1'},
                    'capabilities': {'experimentalApi': True}}), deadline)
                client.send({'method': 'initialized', 'params': {}})
                started = client.response(client.request('thread/start', {'cwd': str(workspace), 'ephemeral': True}), deadline)
                client.response(client.request('turn/start', {'threadId': started['thread']['id'], 'input': [
                    {'type': 'text', 'text': 'Use the isolated portable MCP fixture.'}]}), deadline)
                while True:
                    event = client.receive(deadline)
                    if event.get('method') == 'turn/completed': break
        finally:
            client.close()
            result['sessions'].append({'phase': phase, 'command': command, 'events': client.events,
                                       'approvals': client.approvals, 'stderr': client.errors})

    try:
        if args.vendor == 'codex':
            config = {'model': 'fixture-model', 'model_provider': 'fixture', 'approval_policy': 'on-request' if args.native_approval == 'prompt' else 'never', 'sandbox_mode': 'workspace-write',
                      'model_providers': {'fixture': {'name': 'Local fixture', 'base_url': env['COPILOT_PROVIDER_BASE_URL'],
                          'wire_api': 'responses', 'requires_openai_auth': False, 'supports_websockets': False}},
                      'features': {'plugins': True, 'remote_plugin': False, 'recommended_plugins': False, 'enable_request_compression': False},
                      'projects': {str(workspace): {'trust_level': 'trusted'}}}
            if args.native_approval == 'approve':
                config['plugins'] = {'fixture@oda-mcp-fixture': {'mcp_servers': {'fixture-mcp': {
                    'tools': {'portable_record': {'approval_mode': 'approve'}}}}}}
            (home / filename).write_text(encode(config))
            values = {'plugins': {'fixture@oda-mcp-fixture': {'enabled': True}},
                      'marketplaces': {'oda-mcp-fixture': {'source_type': 'local', 'source': str(market)}}}
        else:
            (home / 'config.json').write_text(json.dumps({'trustedFolders': [str(workspace)], 'autoUpdate': False}))
            values = {'enabledPlugins': {'fixture@oda-mcp-fixture': True},
                      'extraKnownMarketplaces': {'oda-mcp-fixture': {'source': {'source': 'directory', 'path': str(market)}}}}
        canonical = workspace / '.agents/plugins' / namespace
        canonical.mkdir(parents=True)
        (workspace / '.agents/manifest.json').write_text(json.dumps({'version': '1.1.0-draft.2', 'profiles': ['plugins']}))
        (workspace / '.agents/AGENTS.md').write_text('Use the isolated MCP fixture.\n')
        (canonical / 'profile.json').write_text(json.dumps({'namespace': namespace, 'harness_version': '=' + result['native_version'],
            'scope': args.scope, 'required': True, 'artifacts': [{'kind': 'config', 'source': filename}]}))
        selection = canonical / filename
        selection.write_text(encode(values))
        invoke(['go', 'build', '-o', str(cli), './cmd/agents'], repo / 'CLI')
        before = {str(p.relative_to(home)): sha(p) for p in home.rglob('*') if p.is_file()}
        apply()
        if args.scope == 'project':
            assert before == {str(p.relative_to(home)): sha(p) for p in home.rglob('*') if p.is_file()}, 'project apply changed native user files'
        invoke([str(binary), 'plugin', 'add' if args.vendor == 'codex' else 'install', 'fixture@oda-mcp-fixture'])
        session()
        logs = list(home.rglob('oda-portable-mcp-events.jsonl'))
        assert len(logs) == 1, 'expected one native plugin data log'
        log = logs[0]
        events = [json.loads(line) for line in log.read_text().splitlines()]
        result['mcp_events'] = events
        process = next(e for e in events if e.get('event') == 'process')
        data = Path(process['env']['PLUGIN_DATA'])
        installed = Path(process['env']['PLUGIN_ROOT'])
        assert data.is_absolute() and data == log.parent and not data.is_relative_to(installed), 'invalid plugin data directory'
        assert process['cwd'] == str(data) and process['args'] == [str(installed / 'server.py'), str(log)], 'MCP argument or cwd expansion differs'
        assert process['env']['ODA_ROOT'] == str(installed) and process['env']['ODA_DATA'] == str(data), 'MCP environment expansion differs'
        result['environment_observations'] = [e['env'] for e in events if e.get('event') == 'process']
        for observation in result['environment_observations']:
            assert observation['PLUGIN_ROOT'] == str(installed) and observation['PLUGIN_DATA'] == str(data) and observation['ODA_ROOT'] == str(installed), 'plugin root or data directory changed between launches'
        result['standard_environment_conformance'] = all(e['ODA_UNKNOWN'] == '${ODA_AMBIENT}' and e['ODA_DATA'] == e['PLUGIN_DATA'] for e in result['environment_observations'])
        if args.vendor == 'codex':
            assert result['standard_environment_conformance'], 'Codex standard environment semantics differ'
        else:
            assert all(e['ODA_UNKNOWN'] == 'MUST_NOT_EXPAND' for e in result['environment_observations']), 'Copilot unknown expansion observation changed'
            assert any(e['ODA_DATA'] == '${PLUGIN_DATA}' for e in result['environment_observations']), 'Copilot session expansion observation changed'
        completed = [e.get('params', {}).get('item' if args.vendor == 'codex' else 'update', {}) for e in result['sessions'][0]['events']]
        completed = [e for e in completed if e.get('id' if args.vendor == 'codex' else 'toolCallId') == 'fixture-portable-call']
        followups = [r['request'] for r in result['model_requests'] if r['phase'] == 'enabled'][1:]
        approvals = result['sessions'][0]['approvals']
        if args.native_approval == 'prompt':
            assert len(approvals) == 1 and approvals[0]['approved'] == (args.decision == 'allow'), 'native approval request or decision differs'
        else:
            assert not approvals, 'unexpected native approval request'
        expected_execution = args.native_approval != 'never' and args.decision == 'allow'
        if expected_execution:
            assert (data / 'effect.txt').read_text() == 'ODA_PORTABLE_EFFECT', 'MCP tool produced no effect'
            calls = [e for e in events if e.get('method') == 'tools/call']
            assert len(calls) == 1, 'unexpected MCP call count'
            call = calls[0]
            assert call['params']['name'] == 'portable_record' and call['params']['arguments'] == {'marker': 'ODA_PORTABLE_CALL'}, 'MCP call differs'
            if approvals: assert call['recorded_at'] >= approvals[0]['responded_at'], 'MCP executed before approval'
            assert any(e.get('event') == 'result' and e['request_id'] == call['id'] for e in events), 'MCP result is not correlated'
            assert any(e.get('status') == 'completed' and 'ODA_PORTABLE_RESULT' in json.dumps(e) for e in completed), 'native did not report correlated MCP completion'
            assert followups and 'ODA_PORTABLE_RESULT' in json.dumps(followups), 'MCP output did not reach the model'
        else:
            assert not (data / 'effect.txt').exists() and not any(e.get('method') == 'tools/call' for e in events), 'refused MCP call executed'
            assert any(e.get('status') == 'failed' for e in completed), 'native did not report a correlated failure'
            assert 'ODA_PORTABLE_RESULT' not in json.dumps(followups), 'refused result reached the model'
            if args.native_approval == 'never': assert any('approval policy is never' in json.dumps(e) for e in completed), 'native unattended refusal differs'
        assert {name: sha(installed / name) for name in package_hashes} == package_hashes, 'installed package changed'
        log_hash = sha(log)
        if args.vendor == 'codex': values['plugins']['fixture@oda-mcp-fixture']['enabled'] = False
        else: values['enabledPlugins']['fixture@oda-mcp-fixture'] = False
        selection.write_text(encode(values))
        apply()
        phase = 'disabled'
        session()
        disabled = [r['request'] for r in result['model_requests'] if r['phase'] == 'disabled']
        assert disabled and not any('portable_record' in json.dumps(t) for r in disabled for t in r.get('tools', [])), 'disabled plugin tool is still exposed'
        assert sha(log) == log_hash, 'disabled plugin started its MCP server'
        if expected_execution: assert (data / 'effect.txt').read_text() == 'ODA_PORTABLE_EFFECT', 'disable removed plugin data'
        assert {str(p.relative_to(plugin)): sha(p) for p in plugin.rglob('*') if p.is_file()} == package_hashes, 'source package changed'
        result.update(passed=True, installed_path=str(installed), data_path=str(data), installed_package_hashes={name: sha(installed / name) for name in package_hashes})
    except Exception as error:
        result.update(passed=False, error=str(error), traceback=traceback.format_exc())
    finally:
        result['mcp_logs'] = {str(p): p.read_text() for p in home.rglob('oda-portable-mcp-events.jsonl')}
        http.shutdown()
        http.server_close()
        snapshot.write_bytes(Path(__file__).read_bytes())
        output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'passed': result['passed'], 'output': str(output), 'error': result.get('error')}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
