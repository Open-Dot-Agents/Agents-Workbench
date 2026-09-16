#!/usr/bin/env python3
"""Probe native sidekick execution before enabling an adapter mapping."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import traceback

from run_native_approvals import Client, PINS, sha, native_binary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope', choices=['project', 'user'], required=True)
    parser.add_argument('--behavior', choices=['restart', 'persistent'], default='restart')
    parser.add_argument('--experimental', action='store_true')
    parser.add_argument('--expect-ignored', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    snapshot = output.with_suffix('.runner.py')
    assert not output.exists() and not snapshot.exists(), 'refuse evidence replacement'
    binary = native_binary('copilot')
    assert sha(binary) == PINS['copilot'], 'native pin mismatch'
    # Save the source before native execution so a concurrent edit cannot
    # replace the code that produced this attempt.
    with snapshot.open('xb') as stream: stream.write(Path(__file__).read_bytes())
    root = Path(tempfile.mkdtemp(prefix='agents-sidekick-'))
    home, workspace = root/'home/.copilot', root/'workspace'
    home.mkdir(parents=True, mode=0o700)
    workspace.mkdir(mode=0o700)
    subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
    (home/'config.json').write_text(json.dumps({'trustedFolders': [str(workspace)], 'firstLaunchAt': 1234}))
    (home/'settings.json').write_text('{"memory":false}')
    directory = (home if args.scope == 'user' else workspace/'.github')/'agents'
    directory.mkdir(parents=True)
    body = ('---\nname: agents-sidekick-fixture\ndescription: Isolated sidekick probe\ntools: [bash]\n'
            'sidekick:\n  triggers:\n    - event: user.message\n      limit: 1\n'
            f'  behavior: {args.behavior}\n  maxSendsPerTurn: 1\n---\n'
            'AGENTS_SIDEKICK_CONTEXT\nRun only the isolated marker command.\n')
    (directory/'fixture.agent.md').write_text(body)
    marker, probe = root/'effect.txt', root/'effect.py'
    probe.write_text('from pathlib import Path\nPath('+repr(str(marker))+').write_text("AGENTS_SIDEKICK_EFFECT")\n')
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(home.parent), 'COPILOT_HOME': str(home),
           'XDG_STATE_HOME': str(root/'state'), 'COPILOT_CACHE_HOME': str(root/'cache'),
           'COPILOT_OFFLINE': 'true', 'COPILOT_PROVIDER_TYPE': 'openai',
           'COPILOT_PROVIDER_WIRE_API': 'completions', 'COPILOT_MODEL': 'fixture-model'}
    requests, errors = [], []
    lock = threading.Lock()
    result = {'scope': args.scope, 'behavior': args.behavior, 'experimental': args.experimental, 'fixture': str(root),
              'native_version': '1.0.84-9', 'native_sha256': sha(binary), 'runner_sha256': sha(snapshot),
              'helper_sha256': sha(Path(__file__).with_name('run_native_approvals.py')),
              'configuration': body, 'full_adapter_support': False, 'adapter_mapping': False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass

        def do_POST(self):
            try:
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                with lock:
                    requests.append(request)
                    number = len(requests)
                messages = request['messages']
                child = 'AGENTS_SIDEKICK_CONTEXT' in json.dumps(messages)
                responded = any(m.get('role') == 'tool' for m in messages)
                message = {'role': 'assistant', 'content': 'AGENTS_SIDEKICK_DONE' if child else 'AGENTS_PARENT_DONE'}
                finish = 'stop'
                if child and not responded:
                    message = {'role': 'assistant', 'content': None, 'tool_calls': [
                        {'id': f'sidekick-{number}', 'type': 'function', 'function': {
                            'name': 'bash', 'arguments': json.dumps({'command': f'/usr/bin/python3 {probe}',
                                                                  'description': 'Write isolated sidekick marker'})}}]}
                    finish = 'tool_calls'
                data = json.dumps({'id': f'fixture-{number}', 'object': 'chat.completion', 'created': 1,
                                   'model': request['model'], 'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
                                   'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception as error:
                errors.append(str(error))

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env['COPILOT_PROVIDER_BASE_URL'] = f'http://127.0.0.1:{server.server_port}/v1'
    client = None
    try:
        command = [str(binary), '--acp', '--disable-builtin-mcps', '--no-auto-update', '--no-remote']
        if args.experimental: command.append('--experimental')
        result.update(command=command, environment=env)
        client = Client(command, workspace, env, 'allow', str(probe))
        deadline = time.monotonic()+40
        result['initialize'] = client.response(client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {}}), deadline)
        session = client.response(client.request('session/new', {'cwd': str(workspace), 'mcpServers': []}), deadline)
        result['session'] = session
        result['prompt'] = client.response(client.request('session/prompt', {'sessionId': session['sessionId'],
            'prompt': [{'type': 'text', 'text': 'Report that this isolated session has started.'}]}), deadline)
        # Sidekicks run in the background. Keep servicing permissions and native
        # events after the foreground prompt completes.
        observation_end = time.monotonic()+10
        while time.monotonic() < observation_end:
            try: client.receive(observation_end)
            except TimeoutError: break
        result['effect'] = marker.read_text() if marker.exists() else ''
        assert not errors, errors
        if args.expect_ignored:
            logs = {str(p.relative_to(root)): p.read_text() for p in sorted((home/'logs').glob('*.log'))}
            assert any('unknown field ignored: sidekick' in value for value in logs.values()), 'native rejection warning absent'
            assert len(requests) == 1 and result['effect'] == '' and not client.approvals, 'unexpected native child activity'
            task = next(t for t in requests[0]['tools'] if t['function']['name'] == 'task')
            assert 'agents-sidekick-fixture' in task['function']['parameters']['properties']['agent_type']['enum'], 'agent not discovered'
            assert result['prompt']['stopReason'] == 'end_turn', 'foreground session failed'
            result['outcome'] = 'native-ignored'
        else:
            assert result['effect'] == 'AGENTS_SIDEKICK_EFFECT', 'sidekick marker absent'
            calls = {a['params'].get('toolCall', {}).get('toolCallId') for a in client.approvals if a['approved']}
            updates = [e.get('params', {}).get('update', {}) for e in client.events]
            assert any(e.get('toolCallId') in calls and e.get('status') == 'completed'
                       and e.get('_meta', {}).get('github.com/copilot', {}).get('agentId') for e in updates), 'no correlated child command completion'
            result['outcome'] = 'native-executed'
        result['passed'] = True
    except Exception as error:
        result.update(passed=False, error=str(error), traceback=traceback.format_exc())
    finally:
        if client:
            client.close()
            result.update(events=client.events, approvals=client.approvals, stderr=client.errors)
        server.shutdown()
        server.server_close()
        result.update(model_requests=requests, provider_errors=errors)
        result['native_logs'] = {str(p.relative_to(root)): p.read_text() for p in sorted((home/'logs').glob('*.log'))}
        with output.open('x') as stream: json.dump(result, stream, indent=2); stream.write('\n')
    print(json.dumps({'passed': result['passed'], 'output': str(output), 'error': result.get('error')}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
