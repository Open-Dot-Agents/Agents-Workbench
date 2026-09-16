#!/usr/bin/env python3
"""Check user instructions, references, custom source routing, and removal."""
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

from run_native_approvals import Client, PINS, sha, native_binary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=['default-home', 'custom-home', 'custom-source'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    assert not output.exists() and not output.with_suffix('.runner.py').exists()
    repo = Path(__file__).resolve().parents[2]
    binary = native_binary('copilot')
    assert sha(binary) == PINS['copilot']
    base = Path(tempfile.mkdtemp(prefix='agents-user-instructions-'))
    source, target = [base/name for name in ('source', 'target')]
    homes = {label: base/(label+'-home') for label in ('source', 'target')}
    native_homes = {label: home/'.copilot' if args.case == 'default-home' else base/(label+'-native')
                    for label, home in homes.items()}
    body = 'AGENTS_USER_FIRST\n@policy.md\n'
    reference = 'AGENTS_USER_REFERENCE\n@child.md\n'
    child = 'AGENTS_USER_CHILD\n'
    for label, workspace in [('source', source), ('target', target)]:
        workspace.mkdir(mode=0o700)
        subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
        (workspace/'.github').mkdir()
        (workspace/'.github/copilot-instructions.md').write_text('AGENTS_PROJECT_BODY\n')
        (workspace/'fixture.txt').write_text('AGENTS_FILE_READ\n')
        (workspace/'policy.md').write_text('AGENTS_PROJECT_REFERENCE_DECOY\n')
        homes[label].mkdir(mode=0o700)
        home = native_homes[label]
        home.mkdir(mode=0o700)
        (home/'config.json').write_text(json.dumps({'trustedFolders': [str(workspace)], 'firstLaunchAt': 1234}))
        (home/'config.json').chmod(0o600)
        (home/'policy.md').write_text(reference)
        (home/'child.md').write_text(child)
    (native_homes['source']/'copilot-instructions.md').write_text(body)
    result = dict(passed=False, fixture=str(base), case=args.case, scope='user',
                  native_version='1.0.84-9', native_sha256=sha(binary), runner_sha256=sha(__file__),
                  helper_sha256=sha(Path(__file__).with_name('run_native_approvals.py')),
                  implementation_sha256={str(p.relative_to(repo)): sha(p) for p in (repo/'CLI/internal/config').glob('*.go')},
                  commands=[], phases=[], authority_checks=[], full_adapter_support=False)
    requests, offset, active_file = [], 0, None

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(request)
            message, finish = {'role': 'assistant', 'content': 'Fixture complete.'}, 'stop'
            if len(requests)-offset == 1:
                message = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'fixture-read', 'type': 'function',
                           'function': {'name': 'view', 'arguments': json.dumps({'path': str(active_file)})}}]}
                finish = 'tool_calls'
            data = json.dumps({'id': 'fixture', 'object': 'chat.completion', 'created': 1, 'model': request['model'],
                               'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
                               'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def environment(label):
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(homes[label]), 'COPILOT_CACHE_HOME': str(homes[label]/'cache'),
               'COPILOT_OFFLINE': 'true', 'COPILOT_PROVIDER_TYPE': 'openai', 'COPILOT_PROVIDER_WIRE_API': 'completions',
               'COPILOT_MODEL': 'gpt-5.4', 'COPILOT_PROVIDER_BASE_URL': f'http://127.0.0.1:{server.server_port}/v1',
               'XDG_STATE_HOME': str(base/'state')}
        if args.case != 'default-home': env['COPILOT_HOME'] = str(native_homes[label])
        return env

    def command(argv, label='target'):
        run = subprocess.run([str(a) for a in argv], cwd=repo/'CLI', env=os.environ if argv[0] == 'go' else environment(label),
                             capture_output=True, text=True, timeout=90)
        row = dict(command=run.args, exit_code=run.returncode, stdout=run.stdout, stderr=run.stderr)
        result['commands'].append(row)
        assert run.returncode == 0, row
        return row

    def adapter(operation, workspace, label, extra=()):
        before = {name: sha(home/'config.json') for name, home in native_homes.items()}
        row = command([base/'agents', operation, '--vendor', 'copilot', '--root', workspace, '--experimental',
                       '--scope', 'user', '--native-home', native_homes[label], *extra], label)
        after = {name: sha(home/'config.json') for name, home in native_homes.items()}
        result['authority_checks'].append(dict(operation=operation, before=before, after=after))
        assert before == after, 'adapter changed external native state'
        return row

    def native(label, workspace, home_label):
        nonlocal offset, active_file
        offset, active_file = len(requests), workspace/'fixture.txt'
        phase = dict(label=label, workspace=str(workspace), native_home=str(native_homes[home_label]), nonce='AGENTS_USER_'+uuid.uuid4().hex)
        result['phases'].append(phase)
        client = Client([str(binary), '--acp', '--disable-builtin-mcps', '--no-auto-update', '--no-remote'], workspace, environment(home_label), 'deny', '')
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
                path = native_homes[home_label]/'session-state'/phase['session']['sessionId']/'events.jsonl'
                phase['native_events'] = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        assert phase['prompt']['stopReason'] == 'end_turn' and not phase['approvals']
        context = '\n'.join(m['content'] for m in phase['requests'][0]['messages'] if m['role']=='system' and isinstance(m.get('content'), str))
        assert 'AGENTS_PROJECT_BODY' in context and 'AGENTS_FILE_READ' in json.dumps(phase['requests'][1:])
        assert ('AGENTS_USER_REFERENCE' in context) is (label != 'removed')
        assert ('AGENTS_USER_CHILD' in context) is (label != 'removed')
        assert ('AGENTS_USER_UPDATED' in context) is (label == 'updated')
        assert ('AGENTS_USER_FIRST' in context) is (label in ('source', 'relocated'))
        assert 'AGENTS_PROJECT_REFERENCE_DECOY' not in context

    def hashes(directory):
        return {str(p.relative_to(directory)): sha(p) for p in directory.rglob('*') if p.is_file()}

    try:
        native('source', source, 'source')
        command(['go', 'build', '-trimpath', '-buildvcs=false', '-o', base/'agents', './cmd/agents'])
        source_before = hashes(native_homes['source'])
        adapter('import', source, 'source')
        assert 'AGENTS_PROJECT_BODY' not in (source/'.agents/AGENTS.md').read_text()
        profile_relative = Path('native/com.github.copilot/profile.json')
        profile = json.loads((source/'.agents'/profile_relative).read_text())
        artifact = next(a for a in profile['artifacts'] if a['kind']=='instructions')
        if args.case == 'custom-source':
            old = source/'.agents/native/com.github.copilot'/artifact['source']
            artifact['source'] = 'policy/local.md'
            new = source/'.agents/native/com.github.copilot'/artifact['source']
            new.parent.mkdir(); old.rename(new)
            (source/'.agents'/profile_relative).write_text(json.dumps(profile, indent=2)+'\n')
        shutil.copytree(source/'.agents', target/'.agents')
        result['plan'] = json.loads(adapter('plan', target, 'target', ['--format', 'json'])['stdout'])
        adapter('apply', target, 'target')
        result['target_mode'] = (native_homes['target']/'copilot-instructions.md').stat().st_mode & 0o777
        assert result['target_mode'] == 0o600
        native('relocated', target, 'target')
        before = hashes(target/'.agents')
        adapter('import', target, 'target')
        result['roundtrip_preserved'] = before == hashes(target/'.agents')
        assert result['roundtrip_preserved'], 'reimport changed the declared native instruction source'
        selected = target/'.agents/native/com.github.copilot'/artifact['source']
        selected.write_text(body.replace('AGENTS_USER_FIRST', 'AGENTS_USER_UPDATED'))
        adapter('apply', target, 'target')
        native('updated', target, 'target')
        profile['artifacts'] = []
        (target/'.agents'/profile_relative).write_text(json.dumps(profile, indent=2)+'\n')
        adapter('apply', target, 'target', ['--backup'])
        assert not (native_homes['target']/'copilot-instructions.md').exists()
        native('removed', target, 'target')
        result['source_unchanged'] = source_before == hashes(native_homes['source'])
        result['authority_unchanged'] = all(c['before']==c['after'] for c in result['authority_checks'])
        result['native_trust'] = {label: json.loads('\n'.join(line for line in (home/'config.json').read_text().splitlines()
                                                            if not line.lstrip().startswith('//')))['trustedFolders']
                                  for label, home in native_homes.items()}
        assert result['native_trust'] == {'source':[str(source)], 'target':[str(target)]}
        result['references_unchanged'] = all((home/'policy.md').read_text()==reference and (home/'child.md').read_text()==child for home in native_homes.values())
        result['project_unchanged'] = all((workspace/'.github/copilot-instructions.md').read_text()=='AGENTS_PROJECT_BODY\n' for workspace in (source,target))
        assert all(result[k] for k in ('source_unchanged','authority_unchanged','references_unchanged','project_unchanged'))
        result['passed'] = True
    except Exception:
        result['error'] = traceback.format_exc()
    finally:
        server.shutdown(); server.server_close()
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.with_suffix('.runner.py').open('xb') as f: f.write(Path(__file__).read_bytes())
        with output.open('x') as f: json.dump(result,f,indent=2)
    print(json.dumps({'passed':result['passed'],'output':str(output),'error':result.get('error')}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
