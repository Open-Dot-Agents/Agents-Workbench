#!/usr/bin/env python3
"""Observe native path-instruction triggers without an adapter or real provider."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
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
    parser.add_argument('--trigger', choices=['view', 'mention', 'resource', 'edit', 'tracked-view', 'second-turn'], required=True)
    parser.add_argument('--scope', choices=['project', 'user'], default='project')
    parser.add_argument('--pattern', default='**/*.go')
    parser.add_argument('--model', default='gpt-5.4')
    parser.add_argument('--match', choices=['yes', 'no'], default='yes')
    parser.add_argument('--expect-loaded', choices=['yes', 'no'], default='yes')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    assert not output.exists() and not output.with_suffix('.runner.py').exists()
    binary = Path('/home/maurizio/.local/bin/copilot')
    assert sha(binary) == PINS['copilot']
    root = Path(tempfile.mkdtemp(prefix='agents-instruction-triggers-', dir='/mnt/DATA/tmp'))
    workspace, home = root/'workspace', root/'home'
    workspace.mkdir(mode=0o700)
    home.mkdir(mode=0o700)
    subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
    filename = 'fixture.go' if args.match == 'yes' else 'fixture.txt'
    active = workspace/filename
    active.write_text('AGENTS_TRIGGER_FILE_BEFORE\n')
    folder = workspace/'.github/instructions' if args.scope == 'project' else home/'instructions'
    bodies = {}
    for name, tag in [('flat.instructions.md', 'FLAT'), ('nested/deep/fixture.instructions.md', 'NESTED')]:
        path = folder/name
        path.parent.mkdir(parents=True, exist_ok=True)
        body = f'---\napplyTo: "{args.pattern}"\n---\nAGENTS_TRIGGER_{tag}_BODY\n'
        path.write_text(body)
        bodies[str(path)] = {'text': body, 'sha256': sha(path)}
    if args.trigger == 'tracked-view':
        subprocess.run(['git', 'add', '--', filename], cwd=workspace, check=True)
    (home/'config.json').write_text(json.dumps({'trustedFolders': [str(workspace)], 'firstLaunchAt': 1234}))
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'COPILOT_HOME': str(home),
           'COPILOT_CACHE_HOME': str(root/'cache'), 'XDG_STATE_HOME': str(root/'state'),
           'COPILOT_OFFLINE': 'true', 'COPILOT_PROVIDER_TYPE': 'openai',
           'COPILOT_PROVIDER_WIRE_API': 'completions', 'COPILOT_MODEL': args.model}
    record = {'passed': False, 'trigger': args.trigger, 'scope': args.scope, 'pattern': args.pattern,
              'model': args.model, 'match': args.match, 'expected_loaded': args.expect_loaded,
              'fixture': str(root), 'file': str(active), 'definitions': bodies,
              'native_version': '1.0.83', 'native_sha256': sha(binary), 'runner_sha256': sha(__file__),
              'helper_sha256': sha(Path(__file__).with_name('run_native_approvals.py')),
              'adapter_invoked': False, 'full_adapter_support': False, 'requests': [], 'turns': []}
    requests = record['requests']
    patch = f'*** Begin Patch\n*** Update File: {active}\n@@\n-AGENTS_TRIGGER_FILE_BEFORE\n+AGENTS_TRIGGER_FILE_AFTER\n*** End Patch\n'

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(request)
            message, finish = {'role': 'assistant', 'content': 'Fixture complete.'}, 'stop'
            if len(requests) == 1:
                call = {'id': 'trigger-file', 'type': 'function', 'function': {'name': 'view', 'arguments': json.dumps({'path': str(active)})}}
                if args.trigger == 'edit':
                    functions = {t['function']['name']: t['function'] for t in request['tools'] if t['type'] == 'function'}
                    if 'edit' in functions:
                        call['function'] = {'name': 'edit', 'arguments': json.dumps({'path': str(active), 'old_str': 'AGENTS_TRIGGER_FILE_BEFORE', 'new_str': 'AGENTS_TRIGGER_FILE_AFTER'})}
                    else:
                        call = {'id': 'trigger-file', 'type': 'custom', 'custom': {'name': 'apply_patch', 'input': patch}}
                message, finish = {'role': 'assistant', 'content': None, 'tool_calls': [call]}, 'tool_calls'
            body = json.dumps({'id': 'trigger-'+str(len(requests)), 'object': 'chat.completion', 'created': 1,
                               'model': request['model'], 'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
                               'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env['COPILOT_PROVIDER_BASE_URL'] = f'http://127.0.0.1:{server.server_port}/v1'
    client = Client([str(binary), '--acp', '--disable-builtin-mcps', '--no-auto-update', '--no-remote'], workspace, env, 'deny', '')
    try:
        deadline = time.monotonic()+50
        client.response(client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {}}), deadline)
        record['session'] = client.response(client.request('session/new', {'cwd': str(workspace), 'mcpServers': []}), deadline)
        for turn in range(2 if args.trigger == 'second-turn' else 1):
            nonce = 'AGENTS_TRIGGER_'+uuid.uuid4().hex
            text = nonce+' Inspect the fixture file.'
            if args.trigger == 'mention': text += ' Read @'+str(active)
            prompt = [{'type': 'text', 'text': text}]
            if args.trigger == 'resource':
                prompt.append({'type': 'resource_link', 'uri': active.as_uri(), 'name': filename, 'mimeType': 'text/plain'})
            offset = len(requests)
            response = client.response(client.request('session/prompt', {'sessionId': record['session']['sessionId'], 'prompt': prompt}), deadline)
            record['turns'].append({'nonce': nonce, 'prompt': prompt, 'response': response, 'request_offset': offset,
                                    'loaded': {kind: any('AGENTS_TRIGGER_'+kind.upper()+'_BODY' in json.dumps(r['messages']) for r in requests[offset:]) for kind in ('flat', 'nested')}})
            assert response['stopReason'] == 'end_turn'
        context = json.dumps([r['messages'] for r in requests])
        record['loaded'] = {kind: 'AGENTS_TRIGGER_'+kind.upper()+'_BODY' in context for kind in ('flat', 'nested')}
        record['effect'] = active.read_text()
        record['file_observed'] = 'AGENTS_TRIGGER_FILE_BEFORE' in context
        if args.trigger == 'edit':
            assert record['effect'] == 'AGENTS_TRIGGER_FILE_AFTER\n', 'native edit did not produce its file effect'
        else:
            assert record['file_observed'] and record['effect'] == 'AGENTS_TRIGGER_FILE_BEFORE\n'
        assert all(v == (args.expect_loaded == 'yes') for v in record['loaded'].values()), 'native instruction bodies differ from expected loading'
        record['passed'] = True
    except Exception:
        record['error'] = traceback.format_exc()
    finally:
        client.close()
        record.update(events=client.events, approvals=client.approvals, stderr=client.errors)
        if 'session' in record:
            path = home/'session-state'/record['session']['sessionId']/'events.jsonl'
            record['native_events'] = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        server.shutdown()
        server.server_close()
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.with_suffix('.runner.py').open('xb') as f: f.write(Path(__file__).read_bytes())
        with output.open('x') as f: json.dump(record, f, indent=2)
    print(json.dumps({'passed': record['passed'], 'loaded': record.get('loaded'), 'output': str(output), 'error': record.get('error')}))
    return 0 if record['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
