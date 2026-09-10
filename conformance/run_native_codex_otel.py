#!/usr/bin/env python3
"""Measure Codex OTEL scope and prompt export with isolated local services."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import tomllib
import traceback
import uuid

from run_native_approvals import PINS, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--adapter', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    snapshot = output.with_suffix('.runner.py')
    assert not output.exists() and not snapshot.exists(), 'refuse evidence replacement'
    binary = Path('/home/maurizio/.local/bin/codex')
    assert sha(binary) == PINS['codex'], 'native pin mismatch'
    repo = Path(__file__).resolve().parents[2]
    root = Path(tempfile.mkdtemp(prefix='oda-native-codex-otel-', dir='/mnt/DATA/tmp'))
    home, workspace, canonical = [root / p for p in ('home', 'workspace', 'canonical')]
    for path in (home, workspace, canonical): path.mkdir(mode=0o700)
    subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(root / 'host-home'), 'CODEX_HOME': str(home),
           'XDG_STATE_HOME': str(root / 'state'), 'OTEL_BLRP_SCHEDULE_DELAY': '100',
           'OTEL_BSP_SCHEDULE_DELAY': '100'}
    result = {'fixture': str(root), 'native_version': '0.154.0', 'native_sha256': sha(binary),
              'runner_sha256': sha(__file__), 'adapter': args.adapter,
              'helper_sha256': {'run_native_approvals.py': sha(Path(__file__).with_name('run_native_approvals.py'))},
              'implementation_sha256': {str(p.relative_to(repo)): sha(p) for p in sorted((repo / 'CLI/internal/config').glob('*.go'))},
              'commands': [], 'phases': [], 'full_adapter_support': False,
              'limitations': ['Local OTLP HTTP JSON logs and traces only; no TLS, gRPC, binary protocol, or remote collector evidence.',
                              'No metrics delivery, tool execution, or live session reload claim.',
                              'Only synthetic prompts and fixture headers are sent to local collectors.']}
    wire, exports = [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            request = json.loads(body)
            if self.path.startswith('/v1/'):
                wire.append(request)
                rid = 'fixture-' + str(len(wire))
                item = {'type': 'message', 'role': 'assistant', 'id': rid + '-message',
                        'content': [{'type': 'output_text', 'text': 'Fixture complete.'}]}
                events = [{'type': 'response.created', 'response': {'id': rid}},
                          {'type': 'response.output_item.done', 'item': item},
                          {'type': 'response.completed', 'response': {'id': rid, 'usage': {
                              'input_tokens': 0, 'input_tokens_details': None, 'output_tokens': 0,
                              'output_tokens_details': None, 'total_tokens': 0}}}]
                response = ''.join('event: ' + e['type'] + '\ndata: ' + json.dumps(e) + '\n\n' for e in events).encode()
                content_type = 'text/event-stream'
            else:
                exports.append({'path': self.path, 'headers': dict(self.headers), 'body': request})
                response, content_type = b'{}', 'application/json'
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f'http://127.0.0.1:{server.server_port}'

    def invoke(command, cwd=workspace, check=True):
        run = subprocess.run([str(p) for p in command], cwd=cwd, env=None if command[0] == 'go' else env,
                             capture_output=True, text=True, timeout=60)
        record = {'command': [str(p) for p in command], 'exit_code': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr}
        result['commands'].append(record)
        if check: assert run.returncode == 0, json.dumps(record)
        return record

    def config(label, prompt):
        return ('[otel]\nenvironment = ' + json.dumps(label) + '\nlog_user_prompt = ' + str(prompt).lower() + '\nmetrics_exporter = "none"\n' +
                '\n'.join('[otel.' + kind + '.otlp-http]\nendpoint = ' + json.dumps(base_url + '/' + label + '/' + suffix) +
                          '\nprotocol = "json"\nheaders = {"x-oda-fixture" = ' + json.dumps(label) + '}\n'
                          for kind, suffix in [('exporter', 'logs'), ('trace_exporter', 'traces')]))

    try:
        cli = root / 'agents'
        if args.adapter: invoke(['go', 'build', '-o', cli, './cmd/agents'], cwd=repo / 'CLI')
        for label, prompt, project in [('redacted', False, False), ('prompt', True, False), ('project', False, True)]:
            user_text = config(label, prompt)
            if args.adapter:
                source = root / ('source-' + label)
                source.mkdir(mode=0o700)
                (source / 'config.toml').write_text(user_text)
                owner = canonical / label
                owner.mkdir()
                invoke([cli, 'import', '--vendor', 'codex', '--root', owner, '--experimental', '--scope', 'user', '--native-home', source])
                # Each phase uses a separate home so repository ownership stays separate.
                home = root / ('target-' + label)
                home.mkdir(mode=0o700)
                env['CODEX_HOME'] = str(home)
                invoke([cli, 'apply', '--vendor', 'codex', '--root', owner, '--experimental', '--scope', 'user', '--native-home', home])
                again = root / ('again-' + label)
                again.mkdir()
                invoke([cli, 'import', '--vendor', 'codex', '--root', again, '--experimental', '--scope', 'user', '--native-home', home])
                imported = again / '.agents/native/com.openai.codex/config.toml'
                assert tomllib.loads(imported.read_text()) == tomllib.loads(user_text), 'telemetry round trip changed values'
                assert (source / 'config.toml').read_text() == user_text, 'import changed source'
                assert (home / 'config.toml').stat().st_mode & 0o777 == 0o600, 'user config is not private'
            else:
                (home / 'config.toml').write_text(user_text)
            if project:
                project_config = workspace / '.codex/config.toml'
                project_config.parent.mkdir(exist_ok=True)
                project_text = config('ignored-project', True)
                if args.adapter:
                    directory = workspace / '.agents/native/com.openai.codex'
                    directory.mkdir(parents=True)
                    (workspace / '.agents/AGENTS.md').write_text('Use the isolated fixture.\n')
                    (workspace / '.agents/manifest.json').write_text(json.dumps({'version': '1.1.0-draft.2', 'profiles': ['native']}))
                    (directory / 'profile.json').write_text(json.dumps({'namespace': 'com.openai.codex', 'harness_version': '=0.154.0',
                        'scope': 'project', 'required': True, 'artifacts': [{'kind': 'config', 'source': 'config.toml'}]}))
                    (directory / 'config.toml').write_text(project_text)
                    refusal = invoke([cli, 'apply', '--vendor', 'codex', '--root', workspace, '--experimental'], check=False)
                    assert refusal['exit_code'] != 0 and 'otel' in refusal['stderr'], 'ignored project telemetry was accepted'
                    assert not project_config.exists(), 'refused projection wrote project config'
                # Native fixture setup after adapter refusal tests the actual client limitation.
                project_config.write_text(project_text)
                with (home / 'config.toml').open('a') as stream:
                    stream.write('\n[projects.' + json.dumps(str(workspace)) + ']\ntrust_level = "trusted"\n')
            before = sha(home / 'config.toml')
            nonce = 'ODA_OTEL_PROMPT_' + uuid.uuid4().hex
            command = [binary]
            for setting in ['model="fixture-model"', 'model_provider="fixture"', 'features.enable_request_compression=false',
                            'model_providers.fixture.name="Local fixture"', 'model_providers.fixture.wire_api="responses"',
                            'model_providers.fixture.requires_openai_auth=false', 'model_providers.fixture.supports_websockets=false',
                            'model_providers.fixture.base_url=' + json.dumps(base_url + '/v1')]:
                command += ['-c', setting]
            command += ['exec', '--json', nonce]
            start, wire_start = len(exports), len(wire)
            run = invoke(command)
            events = [json.loads(line) for line in run['stdout'].splitlines() if line.startswith('{')]
            phase = {'label': label, 'prompt': nonce, 'events': events, 'exports': exports[start:], 'requests': wire[wire_start:]}
            result['phases'].append(phase)
            assert any(e['type'] == 'turn.completed' for e in events), 'native turn did not complete'
            thread = next(e['thread_id'] for e in events if e['type'] == 'thread.started')
            assert nonce in json.dumps(phase['requests']), 'model did not receive the synthetic prompt'
            logs = [e for e in phase['exports'] if e['path'] == '/' + label + '/logs']
            traces = [e for e in phase['exports'] if e['path'] == '/' + label + '/traces']
            assert logs and traces, 'missing collector requests'
            assert thread in json.dumps(logs), 'logs do not correlate with native thread'
            log_records = [log for export in logs for resource in export['body']['resourceLogs']
                           for scope in resource['scopeLogs'] for log in scope['logRecords']]
            attributes = [{a['key']: a['value'].get('stringValue') for a in log['attributes']} for log in log_records]
            prompts = [a for a in attributes if a.get('event.name') == 'codex.user_prompt' and a.get('conversation.id') == thread]
            assert len(prompts) == 1 and prompts[0]['prompt'] == (nonce if prompt else '[REDACTED]'), 'native prompt event mismatch'
            span_ids = {span['traceId'] for export in traces for resource in export['body']['resourceSpans']
                        for scope in resource['scopeSpans'] for span in scope['spans']}
            assert span_ids & {log['traceId'] for log in log_records}, 'logs and traces do not correlate'
            for export in logs + traces:
                resources = export['body'].get('resourceLogs', []) + export['body'].get('resourceSpans', [])
                assert resources, 'missing telemetry resources'
                for resource in resources:
                    attributes = {a['key']: a['value'] for a in resource['resource']['attributes']}
                    assert attributes['env']['stringValue'] == label, 'environment tag does not match user setting'
            assert (nonce in json.dumps(logs)) == prompt, 'prompt export does not match user setting'
            assert all(e['path'].startswith('/' + label + '/') for e in phase['exports']), 'project collector was used'
            assert all(e['headers'].get('x-oda-fixture') == label for e in phase['exports']), 'static header missing'
            assert sha(home / 'config.toml') == before, 'native execution changed user config'
            phase.update(thread_id=thread, correlated=True, user_config_unchanged=True)
        result['passed'] = True
    except Exception:
        result.update(passed=False, error=traceback.format_exc())
    finally:
        server.shutdown()
        server.server_close()
        snapshot.write_bytes(Path(__file__).read_bytes())
        output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'output': str(output), 'passed': result['passed'], 'error': result.get('error')}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
