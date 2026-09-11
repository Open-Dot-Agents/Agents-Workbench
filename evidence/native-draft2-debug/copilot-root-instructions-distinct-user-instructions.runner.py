#!/usr/bin/env python3
"""Check root instruction import with a pinned Copilot and a local model fixture."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import traceback
import uuid

from run_native_approvals import Client, PINS, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=['root', 'identical', 'distinct', 'reference'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    assert not output.exists() and not output.with_suffix('.runner.py').exists()
    repo = Path(__file__).resolve().parents[2]
    binary = Path('/home/maurizio/.local/bin/copilot')
    assert sha(binary) == PINS['copilot']
    base = Path(tempfile.mkdtemp(prefix='agents-root-instructions-', dir='/mnt/DATA/tmp'))
    source, target, home = [base / n for n in ('source', 'target', 'home')]
    for p in (source, target, home): p.mkdir(mode=0o700)
    for p in (source, target):
        subprocess.run(['git', 'init', '-q', str(p)], check=True)
        (p/'fixture.txt').write_text('AGENTS_FILE_READ\n')
        (p/'policy.md').write_text('AGENTS_REFERENCED_POLICY\n')
        (p/'.github').mkdir()
        (p/'.github/policy.md').write_text('AGENTS_WRONG_REFERENCE_BASE\n')
    (home/'config.json').write_text(json.dumps({'trustedFolders': [str(source), str(target)], 'firstLaunchAt': 1234}))
    (home/'config.json').chmod(0o600)
    body = 'AGENTS_ROOT_INSTRUCTION_MARKER\n'
    if args.case == 'reference': body += '@policy.md\n'
    (source/'AGENTS.md').write_text(body)
    if args.case in ('identical', 'distinct'):
        (source/'.github/copilot-instructions.md').write_text(body if args.case == 'identical' else 'AGENTS_OTHER_INSTRUCTION_MARKER\n')
    result = {'passed': False, 'case': args.case, 'fixture': str(base), 'native_version': '1.0.83',
              'native_sha256': sha(binary), 'runner_sha256': sha(__file__),
              'helper_sha256': sha(Path(__file__).with_name('run_native_approvals.py')),
              'implementation_sha256': {str(p.relative_to(repo)): sha(p) for p in sorted((repo/'CLI/internal/config').glob('*.go'))},
              'commands': [], 'phases': [], 'full_adapter_support': False}
    requests, offset, active_file = [], 0, None

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(request)
            message, finish = {'role': 'assistant', 'content': 'Fixture complete.'}, 'stop'
            if len(requests) - offset == 1:
                message = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'fixture-read', 'type': 'function',
                           'function': {'name': 'view', 'arguments': json.dumps({'path': str(active_file)})}}]}
                finish = 'tool_calls'
            data = json.dumps({'id': 'fixture', 'object': 'chat.completion', 'created': 1, 'model': request['model'],
                               'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
                               'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'COPILOT_HOME': str(home), 'COPILOT_CACHE_HOME': str(home/'cache'),
           'COPILOT_OFFLINE': 'true', 'COPILOT_PROVIDER_TYPE': 'openai', 'COPILOT_PROVIDER_WIRE_API': 'completions',
           'COPILOT_MODEL': 'gpt-5.4', 'COPILOT_PROVIDER_BASE_URL': f'http://127.0.0.1:{server.server_port}/v1',
           'XDG_STATE_HOME': str(base/'state')}

    def command(argv, cwd, expect=0):
        run = subprocess.run([str(a) for a in argv], cwd=cwd, env=os.environ if argv[0] == 'go' else env,
                             capture_output=True, text=True, timeout=90)
        row = dict(command=run.args, exit_code=run.returncode, stdout=run.stdout, stderr=run.stderr)
        result['commands'].append(row)
        assert run.returncode == expect, row
        return row

    def native(label, workspace):
        nonlocal offset, active_file
        offset, active_file = len(requests), workspace/'fixture.txt'
        phase = dict(label=label, workspace=str(workspace), nonce='AGENTS_ROOT_'+uuid.uuid4().hex)
        result['phases'].append(phase)
        client = Client([str(binary), '--acp', '--disable-builtin-mcps', '--no-auto-update', '--no-remote'], workspace, env, 'deny', '')
        try:
            deadline = time.monotonic()+50
            client.response(client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {}}), deadline)
            phase['session'] = client.response(client.request('session/new', {'cwd': str(workspace), 'mcpServers': []}), deadline)
            phase['prompt'] = client.response(client.request('session/prompt', {'sessionId': phase['session']['sessionId'],
                'prompt': [{'type': 'text', 'text': phase['nonce']+' Read the fixture file.'}]}), deadline)
        finally:
            client.close()
            phase.update(events=client.events, approvals=client.approvals, stderr=client.errors, requests=requests[offset:])
            if 'session' in phase:
                path = home/'session-state'/phase['session']['sessionId']/'events.jsonl'
                phase['native_events'] = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        assert phase['prompt']['stopReason'] == 'end_turn' and not phase['approvals']
        context = json.dumps(phase['requests'])
        assert 'AGENTS_ROOT_INSTRUCTION_MARKER' in context and 'AGENTS_FILE_READ' in context
        if args.case == 'distinct': assert 'AGENTS_OTHER_INSTRUCTION_MARKER' in context
        if args.case == 'reference':
            expected, absent = ('AGENTS_WRONG_REFERENCE_BASE', 'AGENTS_REFERENCED_POLICY') if label == 'unconverted' else ('AGENTS_REFERENCED_POLICY', 'AGENTS_WRONG_REFERENCE_BASE')
            assert expected in context and absent not in context

    def adapter(operation, workspace, expect=0):
        argv = [base/'agents', operation, '--vendor', 'copilot', '--root', workspace, '--experimental']
        if operation == 'import': argv += ['--force', '--backup']
        else: argv += ['--format', 'json']
        return command(argv, workspace, expect)

    try:
        native('source', source)
        command(['go', 'build', '-trimpath', '-buildvcs=false', '-o', base/'agents', './cmd/agents'], repo/'CLI')
        before = {str(p.relative_to(source)): sha(p) for p in source.rglob('*') if p.is_file()}
        authority = sha(home/'config.json')
        adapter('import', source)
        core = (source/'.agents/AGENTS.md').read_text()
        expected_core = 'AGENTS_OTHER_INSTRUCTION_MARKER\n' if args.case == 'distinct' else 'Use the selected native profile.\n' if args.case == 'reference' else body
        assert core == expected_core
        if args.case in ('distinct', 'reference'):
            assert (source/'.agents/native/com.github.copilot/agent-instructions/AGENTS.md').read_text() == body
        shutil.copytree(source/'.agents', target/'.agents')
        result['plan'] = json.loads(adapter('plan', target)['stdout'])
        adapter('apply', target)
        assert (target/'.github/copilot-instructions.md').read_text() == core
        if args.case in ('distinct', 'reference'): assert (target/'AGENTS.md').read_text() == body
        native('relocated', target)
        adapter('import', target)
        assert (target/'.agents/AGENTS.md').read_text() == core
        if args.case in ('distinct', 'reference'):
            assert (target/'.agents/native/com.github.copilot/agent-instructions/AGENTS.md').read_text() == body
        result['roundtrip_preserved'] = True
        result['source_unchanged'] = all(sha(source/p) == digest for p, digest in before.items())
        result['authority_unchanged'] = authority == sha(home/'config.json')
        assert result['source_unchanged'] and result['authority_unchanged']
        result['passed'] = True
    except Exception:
        result['error'] = traceback.format_exc()
    finally:
        server.shutdown()
        server.server_close()
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.with_suffix('.runner.py').open('xb') as f: f.write(Path(__file__).read_bytes())
        with output.open('x') as f: json.dump(result, f, indent=2)
    print(json.dumps({'passed': result['passed'], 'output': str(output), 'error': result.get('error')}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
