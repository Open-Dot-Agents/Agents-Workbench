#!/usr/bin/env python3
"""Measure Copilot dispatch preferences with an isolated deterministic provider."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import traceback

from run_native_approvals import Client, PINS, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=['inherit', 'override', 'disabled', 'limits'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    snapshot = output.with_suffix('.runner.py')
    assert not output.exists() and not snapshot.exists(), 'refuse evidence replacement'
    binary = Path('/home/maurizio/.local/bin/copilot')
    assert sha(binary) == PINS['copilot'], 'native pin mismatch'
    repo = Path(__file__).resolve().parents[2]
    root = Path(tempfile.mkdtemp(prefix='oda-native-subagents-', dir='/mnt/DATA/tmp'))
    home, workspace = root / 'home', root / 'workspace'
    for path in (home, workspace): path.mkdir(mode=0o700)
    subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
    directory = workspace / '.agents/native/com.github.copilot'
    directory.mkdir(parents=True)
    (workspace / '.agents/AGENTS.md').write_text('Use isolated fixture data.\n')
    (workspace / '.agents/manifest.json').write_text(json.dumps({'version': '1.1.0-draft.2', 'profiles': ['native']}))
    (directory / 'profile.json').write_text(json.dumps({
        'namespace': 'com.github.copilot', 'harness_version': '=1.0.83', 'scope': 'user', 'required': True,
        'artifacts': [{'kind': 'config', 'source': 'settings.json'},
                      {'kind': 'agent', 'source': 'fixture.md', 'name': 'oda-fixture.agent.md'}]}))
    # An agent name is a native selector. It is not a portable model identity.
    name = 'ODA Fixture'
    agent = '---\nname: ODA Fixture\ndescription: Isolated native dispatch fixture\ntools: [bash, task]\n---\nODA_DISPATCH_CHILD\nRun only the isolated fixture command.\n'
    (directory / 'fixture.md').write_text(agent)
    fields = {'model': 'inherit', 'effortLevel': 'inherit', 'contextTier': 'inherit'}
    if args.case == 'override': fields = {'model': 'gpt-5-mini', 'effortLevel': 'high', 'contextTier': 'long_context'}
    settings = {'effortLevel': 'low', 'subagents': {'agents': {name: fields}}}
    if args.case == 'disabled': settings['subagents']['disabledSubagents'] = [name]
    if args.case == 'limits': settings['subagents'].update(maxDepth=1, maxConcurrency=1)
    (directory / 'settings.json').write_text(json.dumps(settings))
    state_path = home / 'config.json'
    state_path.write_text(json.dumps({'trustedFolders': [str(workspace)], 'firstLaunchAt': 1234}))
    state_path.chmod(0o600)
    marker, probe = root / 'effect.txt', root / 'effect.py'
    probe.write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("ODA_DISPATCH_EFFECT")\n')
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'COPILOT_HOME': str(home),
           'COPILOT_CACHE_HOME': str(root / 'cache'), 'XDG_STATE_HOME': str(root / 'state'),
           'COPILOT_OFFLINE': 'true', 'COPILOT_PROVIDER_TYPE': 'openai',
           'COPILOT_PROVIDER_WIRE_API': 'completions', 'COPILOT_MODEL': 'gpt-5.4'}
    result = {'case': args.case, 'fixture': str(root), 'native_version': '1.0.83', 'native_sha256': sha(binary),
              'runner_sha256': sha(__file__), 'helper_sha256': {'run_native_approvals.py': sha(Path(__file__).with_name('run_native_approvals.py'))},
              'implementation_sha256': {str(p.relative_to(repo)): sha(p) for p in sorted((repo / 'CLI/internal/config').glob('*.go'))},
              'configuration': settings, 'agent_source': agent, 'environment': env,
              'commands': [], 'full_adapter_support': False}
    cli = root / 'agents'

    def invoke(command, cwd=workspace):
        run = subprocess.run([str(x) for x in command], cwd=cwd, env=None if command[0] == 'go' else env,
                             capture_output=True, text=True, timeout=90)
        entry = {'command': [str(x) for x in command], 'exit_code': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr}
        result['commands'].append(entry)
        assert run.returncode == 0, json.dumps(entry)
        return entry

    requests = []
    errors = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass

        def do_POST(self):
            try:
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append(request)
                n = len(requests)
                child = 'ODA_DISPATCH_CHILD' in json.dumps(request['messages'])
                # A tool response in the request means this model loop already
                # received its fixture result. Complete that loop without a retry.
                responded = any(m.get('role') == 'tool' for m in request['messages'])
                call = None
                if n == 1 or args.case == 'limits' and n == 2:
                    call = ('task', {'name': 'fixture', 'agent_type': name, 'description': 'Run isolated dispatch fixture',
                                     'prompt': 'Run the isolated fixture command.', 'mode': 'sync'})
                elif child and not responded:
                    call = ('bash', {'command': f'/usr/bin/python3 {probe}', 'description': 'Write isolated dispatch marker'})
                message, finish = {'role': 'assistant', 'content': 'ODA_DISPATCH_COMPLETED'}, 'stop'
                if call:
                    message = {'role': 'assistant', 'content': None, 'tool_calls': [
                        {'id': f'dispatch-{n}', 'type': 'function', 'function': {'name': call[0], 'arguments': json.dumps(call[1])}}]}
                    finish = 'tool_calls'
                body = json.dumps({'id': f'fixture-{n}', 'object': 'chat.completion', 'created': 1,
                                   'model': request['model'], 'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
                                   'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as error:
                errors.append(str(error))
                self.close_connection = True

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env['COPILOT_PROVIDER_BASE_URL'] = f'http://127.0.0.1:{server.server_port}/v1'
    client = None
    try:
        invoke(['go', 'build', '-o', str(cli), './cmd/agents'], cwd=repo / 'CLI')
        before = sha(state_path)
        invoke([cli, 'apply', '--vendor', 'copilot', '--root', workspace, '--experimental', '--scope', 'user', '--native-home', home])
        result['state_unchanged_by_apply'] = sha(state_path) == before
        assert result['state_unchanged_by_apply'] and not marker.exists(), 'apply modified state or executed the fixture'
        native_settings = home / 'settings.json'
        assert json.loads(native_settings.read_text()) == settings, 'projection changed dispatch settings'
        result['settings_mode'] = native_settings.stat().st_mode & 0o777
        assert result['settings_mode'] == 0o600, 'new user settings are not private'
        client = Client([str(binary), '--acp', '--disable-builtin-mcps', '--no-auto-update', '--no-remote'], workspace, env, 'allow', str(probe))
        deadline = time.monotonic() + 40
        result['initialize'] = client.response(client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {}}), deadline)
        session = client.response(client.request('session/new', {'cwd': str(workspace), 'mcpServers': []}), deadline)
        result['session'] = session
        result['prompt'] = client.response(client.request('session/prompt', {'sessionId': session['sessionId'], 'prompt': [{'type': 'text', 'text': 'Run the isolated native dispatch fixture.'}]}), deadline)
        client.close()
        assert not errors, errors
        task = next(t['function'] for t in requests[0]['tools'] if t.get('function', {}).get('name') == 'task')
        available = task['parameters']['properties']['agent_type']['enum']
        result['available_agents'] = available
        events = []
        for path in (home / 'session-state').glob('*/events.jsonl'):
            events.extend(json.loads(line) for line in path.read_text().splitlines())
        result['native_events'] = events
        started = [e for e in events if e['type'] == 'subagent.started']
        configured = [e for e in events if e['type'] == 'subagent.configured']
        result['configured_agents'] = [e['data'] for e in configured]
        if args.case == 'disabled':
            assert name not in available, 'disabled agent remains exposed in task schema'
            assert not started and not marker.exists(), 'disabled agent executed'
            assert any(e['type'] == 'tool.execution_complete' and e['data'].get('toolCallId') == 'dispatch-1' and not e['data'].get('success') for e in events), 'no correlated disabled dispatch refusal'
        else:
            assert name in available and started and configured, 'selected agent was not dispatched'
            assert marker.read_text() == 'ODA_DISPATCH_EFFECT', 'child effect missing'
            assert any(a['approved'] for a in client.approvals), 'child execution had no fixture approval'
            assert any(e['type'] == 'subagent.completed' and e['data'].get('toolCallId') == 'dispatch-1' for e in events), 'no correlated dispatch completion'
            if args.case == 'limits':
                result['configured_limits_enforced'] = len(started) == 1
                assert len(started) == 2 and len(configured) == 2, 'BYOK limit behavior changed; inspect account prerequisites'
            else:
                expected = 'gpt-5-mini' if args.case == 'override' else 'gpt-5.4'
                result['selected_model_observed'] = all(e['data'].get('model') == expected for e in configured)
                result['wire_models'] = [q['model'] for q in requests]
                result['wire_reasoning_efforts'] = [q.get('reasoning_effort') for q in requests]
                effort = 'high' if args.case == 'override' else 'low'
                tier = 'long_context' if args.case == 'override' else 'default'
                assert result['selected_model_observed'], 'configured model differs from requested model'
                assert all(e['data']['contextTier'] == tier for e in configured), 'configured context-tier event differs'
                assert result['wire_models'] == ['gpt-5.4', expected, expected, 'gpt-5.4'], 'native wire model differs from dispatch selection'
                assert result['wire_reasoning_efforts'] == ['low', effort, effort, 'low'], 'native wire reasoning effort differs'
        again = root / 'reimport'
        source_before = {p.name: {'sha256': sha(p), 'mode': p.stat().st_mode & 0o777} for p in (state_path, native_settings)}
        invoke([cli, 'import', '--vendor', 'copilot', '--root', again, '--experimental', '--scope', 'user', '--native-home', home])
        imported = json.loads((again / '.agents/native/com.github.copilot/settings.json').read_text())
        assert imported == settings, 'reimport changed dispatch preferences'
        source_after = {p.name: {'sha256': sha(p), 'mode': p.stat().st_mode & 0o777} for p in (state_path, native_settings)}
        assert source_before == source_after, 'import wrote native configuration or state'
        result.update(source_before_import=source_before, source_after_import=source_after)
        result['reimported_preferences'] = imported
        result['passed'] = True
    except Exception as error:
        result.update(passed=False, error=str(error), traceback=traceback.format_exc())
    finally:
        if client:
            client.close()
            result.update(events=client.events, approvals=client.approvals, stderr=client.errors)
        server.shutdown()
        server.server_close()
        result.update(model_requests=requests, provider_errors=errors, marker=marker.read_text() if marker.exists() else '')
        snapshot.write_bytes(Path(__file__).read_bytes())
        output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'passed': result['passed'], 'output': str(output), 'error': result.get('error')}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
