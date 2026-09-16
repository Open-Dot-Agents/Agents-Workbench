#!/usr/bin/env python3
"""Test project skill import and relocation through native discovery and execution."""
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
    parser.add_argument('--origin', choices=['github', 'claude', 'agents'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    assert not output.exists() and not output.with_suffix('.runner.py').exists()
    repo = Path(__file__).resolve().parents[2]
    binary = native_binary('copilot')
    assert sha(binary) == PINS['copilot']
    base = Path(tempfile.mkdtemp(prefix='oda-copilot-skill-import-'))
    source, target, home = (base / name for name in ('source', 'target', 'home'))
    for directory in (source, target, home): directory.mkdir(mode=0o700)
    for directory in (source, target): subprocess.run(['git', 'init', '-q', str(directory)], check=True)
    skill = source / ('.'+args.origin) / 'skills/fixture-import'
    (skill / 'scripts').mkdir(parents=True)
    (skill / 'SKILL.md').write_text('---\nname: fixture-import\ndescription: AGENTS_SKILL_IMPORT_DESCRIPTION\n---\nAGENTS_SKILL_IMPORT_BODY\nUse scripts/probe.py and data.txt from this skill directory.\n')
    (skill / 'data.txt').write_text('AGENTS_SKILL_IMPORT_ASSET')
    (skill / 'blob.bin').write_bytes(bytes(range(256)))
    (skill / 'scripts/probe.py').write_text('from pathlib import Path\nPath("effect.txt").write_text((Path(__file__).resolve().parents[1]/"data.txt").read_text())\n')
    (skill / 'scripts/probe.py').chmod(0o700)
    instructions = source / '.github/copilot-instructions.md'
    instructions.parent.mkdir(exist_ok=True)
    instructions.write_text('Use only isolated fixture data.\n')
    (home / 'config.json').write_text(json.dumps({'trustedFolders': [str(source), str(target)], 'firstLaunchAt': 1234}))
    (home / 'config.json').chmod(0o600)
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'COPILOT_HOME': str(home),
           'COPILOT_CACHE_HOME': str(base / 'cache'), 'XDG_STATE_HOME': str(base / 'state'),
           'COPILOT_OFFLINE': 'true', 'COPILOT_PROVIDER_TYPE': 'openai',
           'COPILOT_PROVIDER_WIRE_API': 'completions', 'COPILOT_MODEL': 'gpt-5.4'}
    result = {'passed': False, 'fixture': str(base), 'origin': args.origin, 'scope': 'project',
              'native_version': '1.0.84-9', 'native_sha256': sha(binary), 'runner_sha256': sha(__file__),
              'helper_sha256': sha(Path(__file__).with_name('run_native_approvals.py')),
              'implementation_sha256': {str(p.relative_to(repo)): sha(p) for p in sorted((repo / 'CLI/internal/config').glob('*.go'))},
              'commands': [], 'phases': [], 'full_adapter_support': False}
    requests = []
    active_probe, request_offset = None, 0

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(request)
            call = None
            index = len(requests)-request_offset
            if index == 1:
                call = ('skill', {'skill': 'fixture-import'})
            elif index == 2 and 'AGENTS_SKILL_IMPORT_BODY' in json.dumps(request['messages']):
                call = ('bash', {'command': '/usr/bin/python3 '+str(active_probe), 'description': 'Read the isolated skill asset'})
            message, finish = {'role': 'assistant', 'content': 'AGENTS_SKILL_IMPORT_COMPLETE'}, 'stop'
            if call:
                message = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'import-call-'+str(index), 'type': 'function',
                    'function': {'name': call[0], 'arguments': json.dumps(call[1])}}]}
                finish = 'tool_calls'
            body = json.dumps({'id': 'import-'+str(index), 'object': 'chat.completion', 'created': 1,
                               'model': request['model'], 'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
                               'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env['COPILOT_PROVIDER_BASE_URL'] = f'http://127.0.0.1:{server.server_port}/v1'

    def command(argv, cwd=source):
        run = subprocess.run([str(a) for a in argv], cwd=cwd,
                             env={**os.environ, 'XDG_STATE_HOME': env['XDG_STATE_HOME']} if argv[0] == 'go' else env,
                             capture_output=True, text=True, timeout=60)
        row = {'command': [str(a) for a in argv], 'exit_code': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr}
        result['commands'].append(row)
        assert run.returncode == 0, json.dumps(row)
        return row

    def hashes(directory):
        return {str(p.relative_to(directory)): sha(p) for p in sorted(directory.rglob('*')) if p.is_file()}

    def native(label, workspace, package):
        nonlocal active_probe, request_offset
        request_offset, active_probe = len(requests), package / 'scripts/probe.py'
        phase = {'label': label, 'workspace': str(workspace), 'package': str(package), 'nonce': 'ODA_SKILL_IMPORT_'+uuid.uuid4().hex}
        result['phases'].append(phase)
        phase['discovery'] = json.loads(command([binary, 'skill', 'list', '--json'], cwd=workspace)['stdout'])
        client = Client([str(binary), '--acp', '--disable-builtin-mcps', '--no-auto-update', '--no-remote'], workspace, env, 'allow', str(active_probe))
        try:
            deadline = time.monotonic()+50
            client.response(client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {}}), deadline)
            phase['session'] = client.response(client.request('session/new', {'cwd': str(workspace), 'mcpServers': []}), deadline)
            phase['prompt'] = client.response(client.request('session/prompt', {'sessionId': phase['session']['sessionId'],
                'prompt': [{'type': 'text', 'text': phase['nonce']}]}), deadline)
            assert phase['prompt']['stopReason'] == 'end_turn'
        finally:
            client.close()
            phase.update(events=client.events, approvals=client.approvals, stderr=client.errors, requests=requests[request_offset:])
            marker = workspace / 'effect.txt'
            phase['effect'] = marker.read_text() if marker.exists() else None
            phase['body_loaded'] = any('AGENTS_SKILL_IMPORT_BODY' in json.dumps(r['messages']) for r in phase['requests'])
            if 'session' in phase:
                path = home / 'session-state' / phase['session']['sessionId'] / 'events.jsonl'
                phase['native_events'] = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    try:
        result['source_hashes'] = hashes(skill)
        result['source_modes'] = {str(p.relative_to(skill)): p.stat().st_mode & 0o777
                                  for p in skill.rglob('*') if p.is_file()}
        source_inodes = {str(p.relative_to(skill)): p.stat().st_ino for p in skill.rglob('*') if p.is_file()}
        native('source', source, skill)
        assert result['phases'][0]['body_loaded'] and result['phases'][0]['effect'] == 'AGENTS_SKILL_IMPORT_ASSET'
        cli = base / 'agents'
        command(['go', 'build', '-trimpath', '-buildvcs=false', '-o', cli, './cmd/agents'], cwd=repo / 'CLI')
        before = sha(home / 'config.json')
        command([cli, 'import', '--vendor', 'copilot', '--root', source, '--experimental'])
        result['imported_hashes'] = hashes(source / '.agents/skills/fixture-import')
        report = source / '.agents/native/com.github.copilot/import-report.json'
        result['import_report'] = json.loads(report.read_text()) if report.exists() else None
        shutil.copytree(source / '.agents', target / '.agents')
        result['plan'] = json.loads(command([cli, 'plan', '--vendor', 'copilot', '--root', target, '--experimental', '--format', 'json'])['stdout'])
        command([cli, 'apply', '--vendor', 'copilot', '--root', target, '--experimental'])
        result['external_state_unchanged'] = sha(home / 'config.json') == before
        assert result['external_state_unchanged']
        native('relocated', target, target / '.agents/skills/fixture-import')
        result['projected_hashes'] = hashes(target / '.agents/skills/fixture-import')
        result['projected_modes'] = {str(p.relative_to(target / '.agents/skills/fixture-import')): p.stat().st_mode & 0o777
                                    for p in (target / '.agents/skills/fixture-import').rglob('*') if p.is_file()}
        result['source_unchanged'] = hashes(skill) == result['source_hashes']
        assert result['source_unchanged']
        result['source_modes_preserved'] = result['source_modes'] == {
            str(p.relative_to(skill)): p.stat().st_mode & 0o777 for p in skill.rglob('*') if p.is_file()}
        result['source_inodes_preserved'] = source_inodes == {
            str(p.relative_to(skill)): p.stat().st_ino for p in skill.rglob('*') if p.is_file()}
        assert result['source_modes_preserved'] and result['source_inodes_preserved']
        assert result['imported_hashes'] == result['projected_hashes'] == result['source_hashes'], 'project skill assets were lost during native import'
        assert result['phases'][1]['body_loaded'] and result['phases'][1]['effect'] == 'AGENTS_SKILL_IMPORT_ASSET', 'relocated project skill did not execute'
        command([cli, 'import', '--vendor', 'copilot', '--root', target, '--experimental'])
        result['reimported_hashes'] = hashes(target / '.agents/skills/fixture-import')
        assert result['reimported_hashes'] == result['source_hashes']
        result['passed'] = True
    except Exception:
        result['error'] = traceback.format_exc()
    finally:
        server.shutdown(); server.server_close()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.with_suffix('.runner.py').write_bytes(Path(__file__).read_bytes())
        output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({'passed': result['passed'], 'output': str(output), 'error': result.get('error')}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
