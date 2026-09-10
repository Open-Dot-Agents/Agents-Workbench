#!/usr/bin/env python3
"""Test projected Codex background hooks and context limits with native evidence.

The execution policy is explicit native fixture setup.
Hook files or inline hook settings pass through import, apply, and reimport.
No real credentials, external model, or user stores are used.
"""
import argparse
import json
import os
import signal
from pathlib import Path
import shutil
import shlex
import subprocess
import tempfile
import threading
import time
import tomllib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from run_native_approvals import Client, PINS, sha
from run_native_codex_hooks import toml


HOOK = r'''
import json, pathlib, sys, time, os, subprocess
payload = json.load(sys.stdin)
log, gate, scenario, context_file = sys.argv[1:]
child = None
if scenario in ['background-timeout', 'background-unsubscribe', 'background-shutdown', 'background-archive']:
    child = subprocess.Popen(['/usr/bin/python3', '-c', 'import time; from pathlib import Path; time.sleep(10); Path(' + repr(str(pathlib.Path(log).with_suffix('.child-effect'))) + ').write_text("ODA_CHILD_EFFECT")'])
def record(event):
    with pathlib.Path(log).open('a') as stream:
        stream.write(json.dumps({'event': event, 'input': payload, 'pid': os.getpid(),
                                'time': time.monotonic(), 'scenario': scenario,
                                'child_pid': child.pid if child else None, 'pgid': os.getpgid(0)}) + '\n')
record('start')
if scenario.startswith('background'):
    deadline = time.monotonic() + 20
    while not pathlib.Path(gate).exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    context = 'ODA_BACKGROUND_' + payload['tool_use_id']
else:
    context = pathlib.Path(context_file).read_text()
response = {'hookEventName': payload['hook_event_name'], 'additionalContext': context}
if scenario == 'background-deny':
    response.update(permissionDecision='deny', permissionDecisionReason='ODA_BACKGROUND_DENIAL')
print(json.dumps({'hookSpecificOutput': response}), flush=True)
record('end')
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope', choices=['project', 'user'], required=True)
    parser.add_argument('--location', choices=['file', 'inline'], default='file')
    parser.add_argument('--scenario', choices=['background-next', 'background-active',
                        'background-deny', 'background-timeout', 'background-unsubscribe', 'background-shutdown', 'background-archive',
                        'spill', 'spill-tiny', 'unlimited', 'spill-default', 'spill-mixed'], default='background-next')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    snapshot = output.with_suffix('.runner.py')
    if output.exists() or snapshot.exists():
        raise SystemExit('Refuse to replace evidence')
    binary = Path(shutil.which('codex'))
    assert sha(binary) == PINS['codex'], 'native binary pin mismatch'
    repo = Path(__file__).resolve().parents[2]
    root = Path(tempfile.mkdtemp(prefix='oda-codex-background-', dir='/mnt/DATA/tmp'))
    home, workspace, source = [root / name for name in ['home', 'workspace', 'source']]
    for path in [home, workspace, source]:
        path.mkdir(mode=0o700)
    for path in [workspace, source]:
        subprocess.run(['git', 'init', '-q', str(path)], check=True)
    hook = root / 'hook.py'
    hook.write_text(HOOK)
    log, gate = root / 'hooks.jsonl', root / 'release'
    scratch = root / 'temp'
    scratch.mkdir(mode=0o700)
    context_file = root / 'context.txt'
    context = 'ODA_CONTEXT_HEAD\n' + ''.join(f'line_{i:04d} alpha beta gamma delta epsilon\n' for i in range(2000)) + 'ODA_CONTEXT_TAIL'
    context_file.write_text(context)
    probe, effect = workspace / 'probe.py', workspace / 'effect.txt'
    probe.write_text('from pathlib import Path\nPath(' + repr(str(effect)) +
                     ').write_text("ODA_CODEX_TOOL_EFFECT")\n')
    background = args.scenario.startswith('background')
    hook_command = shlex.join(['/usr/bin/python3', str(hook), str(log), str(gate),
                               args.scenario, str(context_file)])
    handler = {'type': 'command', 'command': hook_command, 'timeout': 15,
               'async': background, 'additionalContextLimit': 0 if background or args.scenario == 'unlimited' else 32 if args.scenario == 'spill-tiny' else 128}
    if args.scenario == 'spill-default':
        handler.pop('additionalContextLimit')
    if args.scenario == 'background-timeout':
        handler['timeout'] = 1
    handlers = [handler]
    if args.scenario == 'spill-mixed':
        handlers.append(dict(handler, additionalContextLimit=0))
    event_name = 'PreToolUse' if background else 'SessionStart'
    config = {'hooks': {event_name: [{'matcher': 'Bash' if background else 'startup', 'hooks': handlers}]}}
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
            if background and (len(requests) == 1 or args.scenario == 'background-active' and len(requests) == 2):
                item = {'type': 'function_call', 'call_id': 'fixture-hook-command-' + str(len(requests)),
                        'name': 'exec_command', 'arguments': json.dumps({
                            'cmd': f'/usr/bin/python3 {probe}', 'workdir': str(workspace)})}
            if args.scenario == 'background-active' and len(requests) == 2:
                gate.touch()
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if log.exists() and any(json.loads(line).get('event') == 'end' for line in log.read_text().splitlines()):
                        break
                    time.sleep(0.02)
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
                     '[features]\nenable_request_compression=false\nhooks=true\nplugins=false\nrecommended_plugins=false\n'
                     '[model_providers.fixture]\nname="Local fixture"\n'
                     f'base_url="http://127.0.0.1:{server.server_port}/v1"\n'
                     'wire_api="responses"\nrequires_openai_auth=false\nsupports_websockets=false\n'
                     '[projects.' + json.dumps(str(workspace)) + ']\ntrust_level="trusted"\n')
    (home / 'config.toml').write_text(native_config)
    before = {str(f.relative_to(home)): sha(f) for f in home.rglob('*') if f.is_file()}
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'CODEX_HOME': str(home),
           'XDG_STATE_HOME': str(root / 'state'), 'TMPDIR': str(scratch)}
    cli = root / 'agents'
    subprocess.run(['go', 'build', '-o', str(cli), './cmd/agents'], cwd=repo / 'CLI', check=True)
    result = {'fixture': str(root), 'scope': args.scope, 'location': args.location,
              'scenario': args.scenario, 'native_version': '0.154.0', 'native_sha256': sha(binary),
              'runner_sha256': sha(__file__), 'cli_sha256': sha(cli), 'configuration': config,
              'external_model': False, 'copied_credentials': False, 'full_adapter_support': False,
              'context_sha256': sha(context_file), 'context_bytes': len(context.encode()), 'context_characters': len(context),
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

    def hook_log():
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def model_text(value):
        if isinstance(value, dict):
            return '\n'.join(model_text(v) for v in value.values())
        if isinstance(value, list):
            return '\n'.join(model_text(v) for v in value)
        return value if isinstance(value, str) else ''

    def process_state(pid):
        path = Path(f'/proc/{pid}/stat')
        if not path.exists():
            return None
        try:
            return path.read_text().rsplit(')', 1)[1].split()[0]
        except FileNotFoundError:
            return None

    def check_execution():
        assert result['completed_turn']['status'] == 'completed', 'native turn failed'
        assert not client.approvals, 'unexpected native approval request under fixture policy'
        if background:
            assert effect.read_text() == 'ODA_CODEX_TOOL_EFFECT', 'trigger command effect absent'
            assert any(e.get('method') == 'item/completed' and
                       e.get('params', {}).get('item', {}).get('type') == 'commandExecution' and
                       e['params']['item'].get('status') == 'completed' and
                       str(probe) in e['params']['item'].get('command', '') for e in client.events), 'no correlated command completion'
            start_deadline = time.monotonic() + 5
            while not hook_log() and time.monotonic() < start_deadline:
                time.sleep(0.02)
            initial = hook_log()
            result['hook_log_at_turn_end'] = initial
            assert initial, 'hook did not start'
            if args.scenario != 'background-active':
                assert len(initial) == 1 and initial[0]['event'] == 'start', 'hook ended before gate release'
                assert 'ODA_BACKGROUND_' not in model_text(requests[-1]['input']), 'unfinished context reached the model'
            if args.scenario in ['background-next', 'background-deny']:
                assert process_state(initial[0]['pid']) not in [None, 'Z'], 'pending hook is not alive'
                gate.touch()
            if args.scenario in ['background-unsubscribe', 'background-shutdown', 'background-archive']:
                if args.scenario == 'background-unsubscribe':
                    result['unsubscribe'] = client.response(client.request('thread/unsubscribe',
                        {'threadId': thread['thread']['id']}), time.monotonic() + 15)
                    assert result['unsubscribe']['status'] == 'unsubscribed', 'thread did not unsubscribe'
                    result['loaded_after_unsubscribe'] = client.response(client.request('thread/loaded/list', {}), time.monotonic() + 10)
                    assert thread['thread']['id'] in result['loaded_after_unsubscribe']['data'], 'unsubscribed thread unexpectedly unloaded'
                    states = {str(pid): process_state(pid) for pid in [initial[0]['pid'], initial[0]['child_pid']]}
                    result['process_states_after_unsubscribe'] = states
                    assert all(state not in [None, 'Z'] for state in states.values()), 'unsubscribe unexpectedly stopped fixture processes'
                    # Unsubscribe has a documented inactivity grace period. It is
                    # not session shutdown. Release only our hook to finish safely.
                    gate.touch()
                    stop_deadline = time.monotonic() + 5
                    while len(hook_log()) < 2 and time.monotonic() < stop_deadline:
                        time.sleep(0.02)
                    assert len(hook_log()) == 2 and len(requests) == 2, 'unsubscribed hook did not finish independently'
                else:
                    if args.scenario == 'background-shutdown':
                        client.process.stdin.close()
                        result['graceful_exit_code'] = client.process.wait(timeout=5)
                        assert result['graceful_exit_code'] == 0, 'native graceful shutdown failed'
                    else:
                        result['archive'] = client.response(client.request('thread/archive',
                            {'threadId': thread['thread']['id']}), time.monotonic() + 10)
                        result['loaded_after_archive'] = client.response(client.request('thread/loaded/list', {}), time.monotonic() + 10)
                        assert thread['thread']['id'] not in result['loaded_after_archive']['data'], 'archived thread still loaded'
                    stop_deadline = time.monotonic() + 3
                    while any(process_state(pid) not in [None, 'Z'] for pid in [initial[0]['pid'], initial[0]['child_pid']]) and time.monotonic() < stop_deadline:
                        time.sleep(0.02)
                    states = {str(pid): process_state(pid) for pid in [initial[0]['pid'], initial[0]['child_pid']]}
                    result['process_states_after_shutdown'] = states
                    assert all(state in [None, 'Z'] for state in states.values()), 'native session shutdown left fixture processes alive'
                    assert len(hook_log()) == 1 and not log.with_suffix('.child-effect').exists(), 'stopped fixture process completed'
            else:
                expected_runs = 2 if args.scenario == 'background-active' else 1
                completion_deadline = time.monotonic() + 5
                if args.scenario == 'background-timeout':
                    while process_state(initial[0]['pid']) not in [None, 'Z'] and time.monotonic() < completion_deadline:
                        time.sleep(0.02)
                    result['process_states_after_timeout'] = {
                        str(pid): process_state(pid) for pid in [initial[0]['pid'], initial[0]['child_pid']]}
                    result['observed_timeout_seconds'] = time.monotonic() - initial[0]['time']
                    assert all(state in [None, 'Z'] for state in result['process_states_after_timeout'].values()), 'native timeout left hook processes alive'
                    assert len(hook_log()) == 1 and not log.with_suffix('.child-effect').exists(), 'timed-out hook or child completed'
                    assert 0.8 <= result['observed_timeout_seconds'] < 3, 'one-second timeout not applied'
                else:
                    while (len([e for e in hook_log() if e['event'] == 'end']) < expected_runs or
                           any(process_state(e['pid']) not in [None, 'Z'] for e in hook_log())) and time.monotonic() < completion_deadline:
                        time.sleep(0.02)
                    assert len([e for e in hook_log() if e['event'] == 'end']) == expected_runs, 'hook completion log absent'
                    if args.scenario != 'background-active':
                        assert len(requests) == 2, 'background result triggered an automatic model request'
                        deadline = time.monotonic() + 20
                        client.response(client.request('turn/start', {'threadId': thread['thread']['id'],
                            'input': [{'type': 'text', 'text': 'Report the fixture result.'}]}), deadline)
                        while client.receive(deadline).get('method') != 'turn/completed':
                            pass
                    assert len(requests) == 3, 'unexpected model request count'
                    assert 'ODA_BACKGROUND_fixture-hook-command-1' in model_text(requests[-1]['input']), 'background context absent at the next model step'
        else:
            assert len(requests) == 1, 'unexpected model request count'
            assert not effect.exists(), 'unexpected command effect'
            received = model_text(requests[0]['input'])
            spills = []
            for path in scratch.rglob('*'):
                if path.is_file() and path.stat().st_size == len(context.encode()) and path.read_bytes() == context.encode():
                    spills.append({'path': str(path), 'sha256': sha(path), 'bytes': path.stat().st_size,
                                   'mode': oct(path.stat().st_mode & 0o777)})
            result['context_spills'] = spills
            if args.scenario == 'unlimited':
                assert context in received and not spills, 'zero limit did not retain full context'
            else:
                assert len(spills) == 1, 'full context spill file absent or duplicated'
                assert spills[0]['path'] in received, 'spill path absent from model input'
                if args.scenario == 'spill-tiny':
                    assert 'ODA_CONTEXT_HEAD' not in received, 'tiny limit unexpectedly retained the head'
                else:
                    assert 'ODA_CONTEXT_HEAD' in received, 'context preview absent'
                if args.scenario == 'spill-mixed':
                    assert context in received, 'unlimited handler was affected by the other limit'
                else:
                    assert context not in received and 'line_1000 alpha beta gamma delta epsilon' not in received, 'context was not shortened'
        runs = [e['params'] for e in client.events if e.get('method') == 'hook/completed']
        result['completed_hook_runs'] = runs
        starts = {e['params']['run']['id'] for e in client.events if e.get('method') == 'hook/started'}
        if background:
            # This app-server path emits no hook/started or hook/completed for async
            # handlers. Correlate native definitions, command events, and hook input.
            assert not runs and not starts, 'unexpected background hook notifications'
            for entry in hook_log():
                assert entry['input']['session_id'] == thread['thread']['id'], 'hook session mismatch'
                assert entry['input']['turn_id'] == result['completed_turn']['id'], 'hook turn mismatch'
                assert entry['input']['hook_event_name'] == 'PreToolUse', 'hook event mismatch'
                assert str(probe) in entry['input']['tool_input']['command'], 'hook command mismatch'
        else:
            assert len(runs) == len(handlers), 'native hook completion absent'
        for entry in runs:
            run = entry['run']
            assert entry['threadId'] == thread['thread']['id'], 'hook thread mismatch'
            assert run['id'] in starts and run['sourcePath'] == str(native_target), 'hook source or start mismatch'
            assert run['handlerType'] == 'command', 'unexpected handler type'
            assert run['executionMode'] == ('async' if background else 'sync'), 'native execution mode mismatch'
            if background:
                assert entry['turnId'] == result['completed_turn']['id'], 'background hook turn mismatch'
                assert any(run['id'].endswith(':' + e['input']['tool_use_id']) for e in hook_log()), 'background tool-call mismatch'
            if args.scenario == 'background-timeout':
                assert run['status'] == 'failed' and 800 <= run['durationMs'] < 3000, 'one-second timeout not applied'
            elif args.scenario != 'background-unsubscribe':
                assert run['status'] == 'completed', 'native hook did not complete'

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
            try:
                connection.response(connection.request('initialize', {
                    'clientInfo': {'name': 'oda-background-hooks', 'version': '0.1.0'},
                    'capabilities': {'experimentalApi': True}}), deadline)
                connection.send({'method': 'initialized', 'params': {}})
            except Exception:
                connection.close()
                raise
            return connection

        client = connect()
        listing = client.response(client.request('hooks/list', {'cwds': [str(workspace)]}),
                                  time.monotonic() + 30)
        result['hooks_list'] = listing
        definitions = [h for entry in listing['data'] for h in entry['hooks']]
        assert len(definitions) == len(handlers), 'unexpected hook count'
        assert all(h['sourcePath'] == str(native_target) and not h['isManaged']
                   for h in definitions), 'unexpected hook source'
        projected = (tomllib.loads if args.location == 'inline' else json.loads)(native_target.read_text())
        assert projected['hooks'] == config['hooks'], 'projected hook definitions changed'
        assert all(h['handlerType'] == 'command' and h['command'] == hook_command for h in definitions), 'unexpected hook command'
        # Trust only the exact isolated source after byte/value and native-source checks.
        assert all(h['trustStatus'] == 'untrusted' for h in definitions), 'unexpected initial trust'
        client.close()
        result['inspection_events'] = client.events
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
        expected = 'trusted'
        assert all(h['trustStatus'] == expected for e in result['execution_hooks_list']['data']
                   for h in e['hooks']), 'native hook trust mismatch'
        thread = client.response(client.request('thread/start', {
            'cwd': str(workspace), 'ephemeral': args.scenario != 'background-archive'}), deadline)
        result['thread'] = thread
        client.response(client.request('turn/start', {'threadId': thread['thread']['id'],
            'input': [{'type': 'text', 'text': 'Run the isolated fixture command.'}]}), deadline)
        while True:
            event = client.receive(deadline)
            if event.get('method') == 'turn/completed':
                result['completed_turn'] = event['params']['turn']
                break
        check_execution()
        result['passed'] = True
    except Exception as error:
        result.update(passed=False, error=str(error))
    finally:
        if client:
            client.close()
            result.update(events=client.events, approvals=client.approvals, stderr=client.errors)
        result['fixture_cleanup'] = []
        for entry in hook_log():
            for pid in [entry['pid'], entry.get('child_pid')]:
                if pid and process_state(pid) not in [None, 'Z']:
                    cmdline = Path(f'/proc/{pid}/cmdline').read_bytes()
                    if str(root).encode() in cmdline:
                        os.kill(pid, signal.SIGKILL)
                        result['fixture_cleanup'].append(pid)
        server.shutdown()
        server.server_close()
        result.update(model_requests=requests, effect=effect.read_text() if effect.exists() else '')
        if log.exists():
            result['hook_events'] = [json.loads(line) for line in log.read_text().splitlines()]
        snapshot.write_bytes(Path(__file__).read_bytes())
        output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'output': str(output), 'passed': result['passed'], 'error': result.get('error')}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
