#!/usr/bin/env python3
"""Check recursive Copilot instructions through import, relocation, and file reads."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
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
    parser.add_argument('--scope', choices=['project', 'user'], required=True)
    parser.add_argument('--match', choices=['yes', 'no'], required=True)
    parser.add_argument('--pattern', default='**/*.go')
    parser.add_argument('--follow-catalog', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    assert not args.follow_catalog or args.pattern == '**/*.go', 'catalog selection covers one fixture pattern only'
    output = args.output.resolve()
    assert not output.exists() and not output.with_suffix('.runner.py').exists()
    repo = Path(__file__).resolve().parents[2]
    binary = Path('/home/maurizio/.local/bin/copilot')
    assert sha(binary) == PINS['copilot']
    base = Path(tempfile.mkdtemp(prefix='agents-recursive-instructions-', dir='/mnt/DATA/tmp'))
    source, target, source_home, target_home = [base / name for name in ('source', 'target', 'source-home', 'target-home')]
    for directory in (source, target, source_home, target_home): directory.mkdir(mode=0o700)
    for workspace in (source, target):
        subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
        (workspace / 'fixture.go').write_text('package fixture // AGENTS_FILE_READ\n')
        (workspace / 'fixture.txt').write_text('AGENTS_FILE_READ\n')
    for home in (source_home, target_home):
        (home / 'config.json').write_text(json.dumps({'trustedFolders': [str(source), str(target)], 'firstLaunchAt': 1234}))
        (home / 'config.json').chmod(0o600)
    def location(workspace, home):
        return workspace / '.github/instructions' if args.scope == 'project' else home / 'instructions'
    instruction_root = location(source, source_home)
    definitions = {
        'flat.instructions.md': '---\napplyTo: "**/*.go"\n---\nAGENTS_FLAT_INSTRUCTION_BODY\n',
        'nested/deep/fixture.instructions.md': '---\napplyTo: "**/*.go"\n---\nAGENTS_NESTED_INSTRUCTION_BODY\n',
    }
    for name, content in definitions.items():
        path = instruction_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.replace('**/*.go', args.pattern))
    env_base = {'PATH': '/usr/bin:/bin', 'COPILOT_OFFLINE': 'true', 'COPILOT_PROVIDER_TYPE': 'openai',
                'COPILOT_PROVIDER_WIRE_API': 'completions', 'COPILOT_MODEL': 'gpt-5.4',
                'XDG_STATE_HOME': str(base / 'state')}
    def environment(home):
        return {**env_base, 'HOME': str(home), 'COPILOT_HOME': str(home), 'COPILOT_CACHE_HOME': str(home / 'cache')}
    result = {'passed': False, 'fixture': str(base), 'scope': args.scope, 'match': args.match, 'pattern': args.pattern,
              'follow_catalog': args.follow_catalog,
              'native_version': '1.0.83', 'native_sha256': sha(binary), 'runner_sha256': sha(__file__),
              'helper_sha256': sha(Path(__file__).with_name('run_native_approvals.py')),
              'implementation_sha256': {str(p.relative_to(repo)): sha(p) for p in sorted((repo / 'CLI/internal/config').glob('*.go'))},
              'commands': [], 'phases': [], 'full_adapter_support': False}
    requests, offset, active_file, phase_catalog, read_queue = [], 0, None, [], []

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            nonlocal phase_catalog, read_queue
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(request)
            index = len(requests) - offset
            if index == 1:
                context = '\n'.join(m.get('content', '') for m in request['messages'] if m['role'] == 'system' and isinstance(m.get('content'), str))
                phase_catalog = [{'pattern': match.group(1), 'path': match.group(2), 'description': match.group(3).strip()}
                                 for match in re.finditer(r"^\| ([^|]+?) \| '([^']+\.instructions\.md)' \|([^|]*)\|$", context, re.MULTILINE)]
                read_queue = []
                if args.follow_catalog and args.match == 'yes':
                    # The fixture model follows the native table for one known
                    # pattern. This is not native glob or policy enforcement.
                    read_queue.extend(row['path'] for row in phase_catalog if row['pattern'] == '**/*.go')
                read_queue.append(str(active_file))
            message, finish = {'role': 'assistant', 'content': 'Fixture complete.'}, 'stop'
            if index <= len(read_queue):
                message = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'read-fixture-'+str(index), 'type': 'function',
                           'function': {'name': 'view', 'arguments': json.dumps({'path': read_queue[index-1]})}}]}
                finish = 'tool_calls'
            body = json.dumps({'id': 'fixture-'+str(index), 'object': 'chat.completion', 'created': 1,
                               'model': request['model'], 'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
                               'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env_base['COPILOT_PROVIDER_BASE_URL'] = f'http://127.0.0.1:{server.server_port}/v1'

    def command(argv, cwd, home):
        argv = [str(a) for a in argv]
        run = subprocess.run(argv, cwd=cwd, env=os.environ if argv[0] == 'go' else environment(home),
                             capture_output=True, text=True, timeout=90)
        row = {'command': argv, 'exit_code': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr}
        result['commands'].append(row)
        assert run.returncode == 0, json.dumps(row)
        return row

    def hashes(directory):
        return {str(p.relative_to(directory)): sha(p) for p in sorted(directory.rglob('*')) if p.is_file()}

    def native(label, workspace, home):
        nonlocal offset, active_file
        offset = len(requests)
        active_file = workspace / ('fixture.go' if args.match == 'yes' else 'fixture.txt')
        phase = {'label': label, 'workspace': str(workspace), 'file': str(active_file), 'nonce': 'AGENTS_INSTRUCTIONS_'+uuid.uuid4().hex}
        result['phases'].append(phase)
        client = Client([str(binary), '--acp', '--disable-builtin-mcps', '--no-auto-update', '--no-remote'], workspace, environment(home), 'deny', '')
        try:
            deadline = time.monotonic()+50
            client.response(client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {}}), deadline)
            phase['session'] = client.response(client.request('session/new', {'cwd': str(workspace), 'mcpServers': []}), deadline)
            phase['prompt'] = client.response(client.request('session/prompt', {'sessionId': phase['session']['sessionId'],
                'prompt': [{'type': 'text', 'text': phase['nonce']+' Read the fixture file.'}]}), deadline)
            assert phase['prompt']['stopReason'] == 'end_turn'
        finally:
            client.close()
            phase.update(events=client.events, approvals=client.approvals, stderr=client.errors, requests=requests[offset:],
                         catalog=phase_catalog, read_queue=read_queue)
            if 'session' in phase:
                path = home / 'session-state' / phase['session']['sessionId'] / 'events.jsonl'
                phase['native_events'] = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
            context = json.dumps(phase['requests'])
            phase['loaded'] = {kind: 'AGENTS_'+kind.upper()+'_INSTRUCTION_BODY' in context for kind in ('flat', 'nested')}
            phase['file_read'] = 'AGENTS_FILE_READ' in context
        assert phase['file_read'] and not phase['approvals']

    def adapter(operation, workspace, home):
        argv = [base/'agents', operation, '--vendor', 'copilot', '--root', workspace, '--experimental', '--scope', args.scope]
        if args.scope == 'user': argv += ['--native-home', home]
        if operation != 'import': argv += ['--format', 'json']
        return command(argv, workspace, home)

    try:
        result['source_hashes'] = hashes(instruction_root)
        native('source', source, source_home)
        assert all(value == (args.match == 'yes') for value in result['phases'][0]['loaded'].values()), 'source instruction selection differs from expected applyTo behavior'
        command(['go', 'build', '-trimpath', '-buildvcs=false', '-o', base/'agents', './cmd/agents'], repo/'CLI', source_home)
        external = {str(home): sha(home/'config.json') for home in (source_home, target_home)}
        adapter('import', source, source_home)
        canonical = source/'.agents/native/com.github.copilot/scoped-instructions'
        result['imported_hashes'] = hashes(canonical)
        shutil.copytree(source/'.agents', target/'.agents')
        result['plan'] = json.loads(adapter('plan', target, target_home)['stdout'])
        adapter('apply', target, target_home)
        result['external_state_unchanged'] = external == {str(home): sha(home/'config.json') for home in (source_home, target_home)}
        assert result['external_state_unchanged']
        native('relocated', target, target_home)
        result['projected_hashes'] = hashes(location(target, target_home))
        result['source_unchanged'] = hashes(instruction_root) == result['source_hashes']
        assert result['source_unchanged']
        assert result['source_hashes'] == result['imported_hashes'] == result['projected_hashes'], 'recursive instruction assets were lost during import'
        assert all(value == (args.match == 'yes') for value in result['phases'][1]['loaded'].values()), 'relocated instruction selection changed'
        adapter('import', target, target_home)
        result['reimported_hashes'] = hashes(target/'.agents/native/com.github.copilot/scoped-instructions')
        assert result['reimported_hashes'] == result['source_hashes']
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
