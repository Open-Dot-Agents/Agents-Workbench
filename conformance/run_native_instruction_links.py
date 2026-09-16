#!/usr/bin/env python3
"""Check canonical instruction-link migration and initial native model context."""
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
    parser.add_argument('--vendor', choices=['codex', 'copilot'], required=True)
    parser.add_argument('--origin', choices=['stable', 'native'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    snapshot = output.with_suffix('.runner.py')
    assert not output.exists() and not snapshot.exists(), 'refuse evidence replacement'
    repo = Path(__file__).resolve().parents[2]
    binary = native_binary(args.vendor)
    assert sha(binary) == PINS[args.vendor], 'native pin mismatch'
    root = Path(tempfile.mkdtemp(prefix='oda-native-instruction-link-'))
    workspace, home = root / 'workspace', root / 'native-home'
    for path in (workspace, home, workspace / '.agents'):
        path.mkdir(mode=0o700)
    subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
    cli = root / 'agents'
    canonical = workspace / '.agents/AGENTS.md'
    manifest = workspace / '.agents/manifest.json'
    link = workspace / 'AGENTS.md'
    duplicate = workspace / '.github/copilot-instructions.md'
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(root / 'host-home'),
           'CODEX_HOME': str(home), 'COPILOT_HOME': str(home),
           'XDG_STATE_HOME': str(root / 'state')}
    result = {'vendor': args.vendor, 'origin': args.origin, 'fixture': str(root),
              'native_version': '0.154.0' if args.vendor == 'codex' else '1.0.84-9',
              'native_sha256': sha(binary), 'runner_sha256': sha(__file__),
              'helper_sha256': {'run_native_approvals.py': sha(Path(__file__).with_name('run_native_approvals.py'))},
              'implementation_sha256': {str(p.relative_to(repo)): sha(p) for p in sorted((repo / 'CLI/internal/config').glob('*.go'))},
              'commands': [], 'phases': [], 'full_adapter_support': False,
              'limits': ['Project scope on Linux only.', 'Fresh native sessions; live reload is not tested.',
                         'Model input and correlated completion only; no native tool execution.',
                         'Trust is set only by this isolated fixture, never by the adapter.']}
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(request)
            identifier = 'fixture-' + str(len(requests))
            if args.vendor == 'codex':
                item = {'type': 'message', 'role': 'assistant', 'id': identifier + '-message',
                        'content': [{'type': 'output_text', 'text': 'Fixture complete.'}]}
                events = [{'type': 'response.created', 'response': {'id': identifier}},
                          {'type': 'response.output_item.done', 'item': item},
                          {'type': 'response.completed', 'response': {'id': identifier, 'usage': {
                              'input_tokens': 0, 'input_tokens_details': None, 'output_tokens': 0,
                              'output_tokens_details': None, 'total_tokens': 0}}}]
                data = ''.join('event: ' + e['type'] + '\ndata: ' + json.dumps(e) + '\n\n' for e in events).encode()
                content_type = 'text/event-stream'
            else:
                data = json.dumps({'id': identifier, 'object': 'chat.completion', 'created': 1,
                                   'model': 'fixture-model', 'choices': [{'index': 0, 'finish_reason': 'stop',
                                       'message': {'role': 'assistant', 'content': 'Fixture complete.'}}],
                                   'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}).encode()
                content_type = 'application/json'
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=http.serve_forever, daemon=True).start()

    def invoke(command, cwd=workspace):
        command = [str(p) for p in command]
        run = subprocess.run(command, cwd=cwd, env=None if command[0] == 'go' else env,
                             capture_output=True, text=True, timeout=90)
        record = {'command': command, 'exit_code': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr}
        result['commands'].append(record)
        assert run.returncode == 0, json.dumps(record)
        return record

    def adapter(operation, experimental=True):
        return invoke([cli, operation, '--vendor', args.vendor, '--root', workspace, '--format', 'json'] +
                      (['--experimental'] if experimental else []))

    def observe(phase):
        if args.vendor == 'codex':
            command = [str(binary)]
            for setting in ['model="fixture-model"', 'model_provider="fixture"',
                            'features.enable_request_compression=false', 'model_providers.fixture.name="Local fixture"',
                            'model_providers.fixture.wire_api="responses"', 'model_providers.fixture.requires_openai_auth=false',
                            'model_providers.fixture.supports_websockets=false',
                            f'model_providers.fixture.base_url="http://127.0.0.1:{http.server_port}/v1"']:
                command += ['-c', setting]
            command += ['app-server', '--listen', 'stdio://']
        else:
            command = [str(binary), '--acp', '--disable-builtin-mcps', '--no-auto-update', '--no-remote']
        client = Client(command, workspace, env, 'deny', '')
        phase['native_command'] = command
        start = len(requests)
        try:
            deadline = time.monotonic() + 35
            if args.vendor == 'codex':
                client.response(client.request('initialize', {'clientInfo': {'name': 'oda-instruction-links', 'version': '1'},
                                                               'capabilities': {'experimentalApi': True}}), deadline)
                client.send({'method': 'initialized'})
                thread = client.response(client.request('thread/start', {'cwd': str(workspace), 'ephemeral': True}), deadline)
                phase['session_id'] = thread['thread']['id']
                client.response(client.request('turn/start', {'threadId': phase['session_id'],
                                'input': [{'type': 'text', 'text': 'Say fixture complete.'}]}), deadline)
                while True:
                    event = client.receive(deadline)
                    if event.get('method') == 'turn/completed':
                        phase['completion'] = event['params']['turn']
                        assert phase['completion']['status'] == 'completed'
                        break
            else:
                client.response(client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {}}), deadline)
                session = client.response(client.request('session/new', {'cwd': str(workspace), 'mcpServers': []}), deadline)
                phase['session_id'] = session['sessionId']
                phase['completion'] = client.response(client.request('session/prompt', {'sessionId': phase['session_id'],
                    'prompt': [{'type': 'text', 'text': 'Say fixture complete.'}]}), deadline)
                assert phase['completion']['stopReason'] == 'end_turn'
            phase['model_requests'] = requests[start:]
            assert phase['model_requests'], 'no model request'
            field = 'input' if args.vendor == 'codex' else 'messages'
            context = json.dumps(phase['model_requests'][0][field])
            phase['instruction_marker_count'] = context.count(phase['marker'])
            assert phase['instruction_marker_count'] == 1, 'instructions missing or duplicated'
            if phase['label'] == 'updated':
                assert 'ODA_CANONICAL_FIRST_MARKER' not in context, 'stale instructions survived update'
            assert not client.approvals, 'unexpected tool approval request'
        finally:
            client.close()
            phase.update(events=client.events, stderr=client.errors)
            phase.setdefault('model_requests', requests[start:])

    try:
        invoke(['go', 'build', '-o', cli, './cmd/agents'], cwd=repo / 'CLI')
        canonical.write_text('Fixture instructions: ODA_CANONICAL_FIRST_MARKER.\n')
        manifest.write_text(json.dumps({'version': '1.0.0' if args.origin == 'stable' else '1.1.0-draft.2', 'profiles': []}))
        adapter('apply', experimental=args.origin == 'native')
        if args.origin == 'native':
            if args.vendor == 'codex':
                link.unlink()  # Fixture replaces the old owned copy with the canonical link.
            link.symlink_to('.agents/AGENTS.md')
        assert link.is_symlink(), 'initial link missing'
        identity = (link.lstat().st_dev, link.lstat().st_ino)
        if args.vendor == 'codex':
            trust = home / 'config.toml'
            trust.write_text('[projects.' + json.dumps(str(workspace)) + ']\ntrust_level="trusted"\n')
        else:
            trust = home / 'config.json'
            trust.write_text(json.dumps({'trustedFolders': [str(workspace)]}))
            env.update(COPILOT_OFFLINE='true', COPILOT_PROVIDER_TYPE='openai', COPILOT_PROVIDER_WIRE_API='completions',
                       COPILOT_MODEL='fixture-model', COPILOT_PROVIDER_BASE_URL=f'http://127.0.0.1:{http.server_port}/v1')
        trust_hash = sha(trust)
        manifest.write_text('{"version":"1.1.0-draft.2","profiles":[]}\n')
        for label, marker in [('first', 'ODA_CANONICAL_FIRST_MARKER'), ('updated', 'ODA_CANONICAL_UPDATED_MARKER')]:
            canonical.write_text('Fixture instructions: ' + marker + '.\n')
            phase = {'label': label, 'marker': marker}
            result['phases'].append(phase)
            native_before = {str(p.relative_to(home)): sha(p) for p in home.rglob('*') if p.is_file()}
            phase['apply'] = json.loads(adapter('apply')['stdout'])
            phase['user_files_unchanged_by_apply'] = native_before == {str(p.relative_to(home)): sha(p) for p in home.rglob('*') if p.is_file()}
            assert phase['user_files_unchanged_by_apply'], 'project apply changed native home'
            phase['link_preserved'] = link.is_symlink() and identity == (link.lstat().st_dev, link.lstat().st_ino)
            assert phase['link_preserved'] and not duplicate.exists(), 'link replaced or duplicate retained'
            phase['plan'] = json.loads(adapter('plan')['stdout'])
            assert not phase['plan']['actions'], 'repeat plan changes files'
            imported = invoke([cli, 'import', '--vendor', args.vendor, '--root', workspace, '--experimental'])
            phase['import'] = imported
            phase['canonical_after_import'] = canonical.read_text()
            assert canonical.read_text() == 'Fixture instructions: ' + marker + '.\n', 'import changed instructions'
            assert link.is_symlink() and identity == (link.lstat().st_dev, link.lstat().st_ino), 'import replaced link'
            phase['plan_after_import'] = json.loads(adapter('plan')['stdout'])
            assert not phase['plan_after_import']['actions'], 'reimport changed the projection'
            observe(phase)
            phase['native_state_changed_during_session'] = sha(trust) != trust_hash
            if args.vendor == 'codex':
                assert sha(trust) == trust_hash, 'native fixture trust changed'
            else:
                # Copilot records first-launch metadata in its JSONC state file.
                state = json.loads('\n'.join(line for line in trust.read_text().splitlines()
                                             if not line.lstrip().startswith('//')))
                phase['native_trust_after_session'] = state['trustedFolders']
                assert state['trustedFolders'] == [str(workspace)], 'native fixture trust changed'
        result['passed'] = True
    except Exception as error:
        result.update(passed=False, error=str(error), traceback=traceback.format_exc())
    finally:
        http.shutdown()
        http.server_close()
        output.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(Path(__file__).read_bytes())
        output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'passed': result['passed'], 'output': str(output), 'error': result.get('error')}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
