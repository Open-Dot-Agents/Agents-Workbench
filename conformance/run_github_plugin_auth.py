#!/usr/bin/env python3
"""Probe the pinned GitHub plugin's bearer reference with local test services.

Only the package copy's MCP URL changes. No real token or GitHub request is
used. A completed native turn is distinct from a working authenticated server.
"""
import argparse
import json
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from run_native_approvals import Client, PINS, sha
from run_native_codex_hooks import toml


def expected_authentication(vendor, token_present, server):
    """Use the tested package fields, including an already bundled header."""
    bearer = vendor == 'codex' and server.get('bearer_token_env_var') == 'GITHUB_PAT_TOKEN'
    header = server.get('headers', {}).get('Authorization') == 'Bearer ${GITHUB_PAT_TOKEN}'
    return token_present and (bearer or header)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vendor', required=True, choices=['codex', 'copilot'])
    parser.add_argument('--token', required=True, choices=['present', 'missing'])
    parser.add_argument('--copilot-header-overlay', action='store_true',
                        help='convert the Codex bearer environment field to a Copilot header reference')
    parser.add_argument('--shared-header-fallback', action='store_true',
                        help='retain the Codex bearer field and add the Copilot header reference')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    snapshot = output.with_suffix('.runner.py')
    assert not output.exists() and not snapshot.exists(), 'refuse evidence replacement'
    repo = Path(__file__).resolve().parents[2]
    source = repo / '.agents/plugins/com.openai.codex/plugins/github'
    provenance = json.loads((source.parents[1] / 'provenance.json').read_text())
    assert all(sha(source / p) == h for p, h in provenance['files'].items())
    binary = Path(shutil.which(args.vendor))
    assert sha(binary) == PINS[args.vendor], 'native version pin mismatch'
    root = Path(tempfile.mkdtemp(prefix='oda-github-auth-'))
    home, workspace, market = [root / p for p in ['home', 'workspace', 'market']]
    for p in (home, workspace, market):
        p.mkdir(mode=0o700)
    subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
    package = market / 'github'
    shutil.copytree(source, package)
    result = {'vendor': args.vendor, 'native_version': '0.154.0' if args.vendor == 'codex' else '1.0.84-9',
              'native_sha256': sha(binary), 'runner_sha256': sha(__file__),
              'helper_sha256': {name: sha(Path(__file__).with_name(name)) for name in
                               ['run_native_approvals.py', 'run_native_codex_hooks.py']},
              'source_revision': provenance['revision'], 'source_files': provenance['files'],
              'fixture': str(root), 'token_present': args.token == 'present',
              'real_credentials': False, 'external_model': False, 'external_github': False,
              'package_mutation': 'The MCP URL points to the local test server.' +
                  (' Copilot bearer_token_env_var is converted to an Authorization environment reference.'
                   if args.copilot_header_overlay else
                   ' A Copilot Authorization environment reference is added beside the Codex bearer field.'
                   if args.shared_header_fallback else ''),
              'commands': [], 'http_requests': [], 'model_requests': [], 'passed': False,
              'full_adapter_support': False}

    class HTTP(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.send_error(405)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if self.path == '/mcp':
                authorized = self.headers.get('Authorization') == 'Bearer oda-synthetic-probe'
                result['http_requests'].append({'method': body.get('method'), 'id': body.get('id'),
                                                'authorized': authorized})
                if not authorized:
                    self.send_error(401)
                    return
                if 'id' not in body:
                    self.send_response(202)
                    self.end_headers()
                    return
                if body['method'] == 'initialize':
                    value = {'protocolVersion': body['params']['protocolVersion'], 'capabilities': {'tools': {}},
                             'serverInfo': {'name': 'oda-github-auth', 'version': '1'}}
                elif body['method'] == 'tools/list':
                    value = {'tools': [{'name': 'oda_auth_probe', 'description': 'Local authentication probe.',
                                        'inputSchema': {'type': 'object', 'properties': {}}}]}
                else:
                    value = {}
                data = json.dumps({'jsonrpc': '2.0', 'id': body['id'], 'result': value}).encode()
                content_type = 'application/json'
            else:
                result['model_requests'].append(body)
                if args.vendor == 'copilot':
                    data = json.dumps({'id': 'oda-local-model', 'object': 'chat.completion', 'created': 1,
                                       'model': 'fixture-model', 'choices': [{'index': 0, 'finish_reason': 'stop',
                                       'message': {'role': 'assistant', 'content': 'Probe complete.'}}],
                                       'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}).encode()
                    content_type = 'application/json'
                else:
                    item = {'type': 'message', 'role': 'assistant', 'id': 'oda-local-message',
                            'content': [{'type': 'output_text', 'text': 'Probe complete.'}]}
                    events = [{'type': 'response.created', 'response': {'id': 'oda-local-response'}},
                              {'type': 'response.output_item.done', 'item': item},
                              {'type': 'response.completed', 'response': {'id': 'oda-local-response',
                               'usage': {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0,
                                         'input_tokens_details': None, 'output_tokens_details': None}}}]
                    data = ''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events).encode()
                    content_type = 'text/event-stream'
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(('127.0.0.1', 0), HTTP)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_port}'
    mcp = json.loads((package / '.mcp.json').read_text())
    mcp['mcpServers']['github']['url'] = url + '/mcp'
    if args.copilot_header_overlay:
        assert args.vendor == 'copilot', 'the header overlay is Copilot-specific'
        server_config = mcp['mcpServers']['github']
        assert server_config.pop('bearer_token_env_var') == 'GITHUB_PAT_TOKEN'
        server_config['headers'] = {'Authorization': 'Bearer ${GITHUB_PAT_TOKEN}'}
    if args.shared_header_fallback:
        server_config = mcp['mcpServers']['github']
        assert server_config['bearer_token_env_var'] == 'GITHUB_PAT_TOKEN'
        server_config['headers'] = {'Authorization': 'Bearer ${GITHUB_PAT_TOKEN}'}
    (package / '.mcp.json').write_text(json.dumps(mcp))
    catalog = {'name': 'oda-github-auth', 'owner': {'name': 'Fixture'},
               'plugins': [{'name': 'github', 'source': './github'}]}
    for p in [market / 'marketplace.json', market / '.agents/plugins/marketplace.json']:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(catalog))
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'CODEX_HOME': str(home),
           'COPILOT_HOME': str(home), 'COPILOT_CACHE_HOME': str(root / 'cache'),
           'XDG_STATE_HOME': str(root / 'state'), 'COPILOT_OFFLINE': 'true',
           'COPILOT_PROVIDER_TYPE': 'openai', 'COPILOT_PROVIDER_WIRE_API': 'completions',
           'COPILOT_PROVIDER_BASE_URL': url + '/v1', 'COPILOT_MODEL': 'fixture-model'}
    if args.token == 'present':
        env['GITHUB_PAT_TOKEN'] = 'oda-synthetic-probe'
    if args.vendor == 'codex':
        config = {'model': 'fixture-model', 'model_provider': 'fixture', 'approval_policy': 'never',
                  'features': {'plugins': True, 'apps': False, 'remote_plugin': False,
                               'recommended_plugins': False, 'enable_request_compression': False},
                  'projects': {str(workspace): {'trust_level': 'trusted'}},
                  'model_providers': {'fixture': {'name': 'Local model', 'base_url': url + '/v1',
                      'wire_api': 'responses', 'requires_openai_auth': False, 'supports_websockets': False}}}
        (home / 'config.toml').write_text('\n'.join(json.dumps(k)+' = '+toml(v) for k,v in config.items())+'\n')
    else:
        (home / 'config.json').write_text(json.dumps({'trustedFolders': [str(workspace)], 'autoUpdate': False}))
    client = None
    try:
        for command in [[str(binary), 'plugin', 'marketplace', 'add', str(market)],
                        [str(binary), 'plugin', 'add' if args.vendor == 'codex' else 'install', 'github@oda-github-auth']]:
            run = subprocess.run(command, cwd=workspace, env=env, capture_output=True, text=True, timeout=30)
            result['commands'].append({'command': command, 'exit_code': run.returncode,
                                       'stdout': run.stdout, 'stderr': run.stderr})
            assert run.returncode == 0, 'native package setup failed'
        command = [str(binary), 'app-server', '--stdio'] if args.vendor == 'codex' else [
            str(binary), '--acp', '--disable-builtin-mcps', '--no-auto-update', '--no-remote']
        client = Client(command, workspace, env, 'deny', '')
        deadline = time.monotonic() + 40
        if args.vendor == 'codex':
            client.response(client.request('initialize', {'clientInfo': {'name': 'oda-github', 'version': '1'},
                            'capabilities': {'experimentalApi': True}}), deadline)
            client.send({'method': 'initialized', 'params': {}})
            started = client.response(client.request('thread/start', {'cwd': str(workspace), 'ephemeral': True}), deadline)
            client.response(client.request('turn/start', {'threadId': started['thread']['id'], 'input': [
                {'type': 'text', 'text': 'Reply Probe complete. Do not call tools.'}]}), deadline)
            while client.receive(deadline).get('method') != 'turn/completed':
                pass
        else:
            client.response(client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {}}), deadline)
            started = client.response(client.request('session/new', {'cwd': str(workspace), 'mcpServers': []}), deadline)
            result['completion'] = client.response(client.request('session/prompt', {
                'sessionId': started['sessionId'], 'prompt': [{'type': 'text', 'text': 'Reply Probe complete. Do not call tools.'}]}), deadline)
        result['native_turn_completed'] = True
        result['authenticated_tools_list'] = any(r['method'] == 'tools/list' and r['authorized'] for r in result['http_requests'])
        result['probe_in_model_context'] = 'oda_auth_probe' in json.dumps(result['model_requests'])
        assert result['model_requests'], 'no correlated local model request'
        expected = expected_authentication(args.vendor, args.token == 'present', mcp['mcpServers']['github'])
        assert result['authenticated_tools_list'] == expected, 'bearer reference behavior differs from expected result'
        assert result['probe_in_model_context'] == expected, 'native tool visibility differs from HTTP observation'
        if args.vendor == 'copilot' and not expected_authentication(args.vendor, True, mcp['mcpServers']['github']):
            assert result['http_requests'], 'Copilot did not attempt MCP discovery'
            assert not any(r['authorized'] for r in result['http_requests']), 'Copilot authorization behavior changed'
        result['passed'] = True
    except Exception as error:
        result['error'] = str(error)
    finally:
        if client:
            client.close()
            result.update(native_events=client.events, native_stderr=client.errors)
        server.shutdown()
        server.server_close()
        output.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(Path(__file__).read_bytes())
        output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({k: result.get(k) for k in ['vendor', 'token_present', 'passed', 'authenticated_tools_list', 'error']}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
