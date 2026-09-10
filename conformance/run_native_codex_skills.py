#!/usr/bin/env python3
"""Measure projected Codex skill controls and imported path relocation."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import tomllib
import traceback

from run_native_approvals import Client, PINS, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope', choices=['project', 'user'], required=True)
    parser.add_argument('--project-selector', choices=['relative', 'absolute'], default='relative')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    snapshot = output.with_suffix('.runner.py')
    assert not output.exists() and not snapshot.exists(), 'refuse evidence replacement'
    binary = Path('/home/maurizio/.local/bin/codex')
    assert sha(binary) == PINS['codex'], 'native pin mismatch'
    repo = Path(__file__).resolve().parents[2]
    root = Path(tempfile.mkdtemp(prefix='oda-native-codex-skills-', dir='/mnt/DATA/tmp'))
    source, home, workspace, canonical = [root / p for p in ('source', 'target', 'workspace', 'canonical')]
    for path in (source, home, workspace, canonical): path.mkdir(mode=0o700)
    if args.scope == 'project': canonical = workspace
    subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
    skill_text = '---\nname: oda-skill-reference\ndescription: Isolated native skill-reference fixture.\n---\nODA_SKILL_REFERENCE_BODY\n'
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(root / 'host-home'), 'CODEX_HOME': str(home),
           'XDG_STATE_HOME': str(root / 'state')}
    cli = root / 'agents'
    result = {'scope': args.scope, 'project_selector': args.project_selector, 'fixture': str(root), 'native_version': '0.154.0', 'native_sha256': sha(binary),
              'runner_sha256': sha(__file__), 'helper_sha256': {'run_native_approvals.py': sha(Path(__file__).with_name('run_native_approvals.py'))},
              'implementation_sha256': {str(p.relative_to(repo)): sha(p) for p in sorted((repo / 'CLI/internal/config').glob('*.go'))},
              'commands': [], 'discoveries': [], 'full_adapter_support': False,
              'limitations': ['Discovery and initial model context only; no skill-script execution.',
                              'Native HOME expressions and references outside copied packages remain external.',
                              'No system-package ownership or source-home state import.']}
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(request)
            rid = 'fixture-' + str(len(requests))
            item = {'type': 'message', 'role': 'assistant', 'id': rid + '-message',
                    'content': [{'type': 'output_text', 'text': 'Fixture complete.'}]}
            events = [{'type': 'response.created', 'response': {'id': rid}},
                      {'type': 'response.output_item.done', 'item': item},
                      {'type': 'response.completed', 'response': {'id': rid, 'usage': {
                          'input_tokens': 0, 'input_tokens_details': None, 'output_tokens': 0,
                          'output_tokens_details': None, 'total_tokens': 0}}}]
            body = ''.join('event: ' + e['type'] + '\ndata: ' + json.dumps(e) + '\n\n' for e in events).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def invoke(command, cwd=workspace, check=True):
        run = subprocess.run([str(p) for p in command], cwd=cwd, env=None if command[0] == 'go' else env,
                             capture_output=True, text=True, timeout=90)
        record = {'command': [str(p) for p in command], 'exit_code': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr}
        result['commands'].append(record)
        if check: assert run.returncode == 0, json.dumps(record)
        return record

    def adapter(operation, owner, native_home):
        command = [cli, operation, '--vendor', 'codex', '--root', owner, '--scope', args.scope, '--experimental']
        if args.scope == 'user': command += ['--native-home', native_home]
        invoke(command)

    def discover(native_home, path, enabled, phase):
        command = [str(binary)]
        for setting in ['model="fixture-model"', 'model_provider="fixture"',
                        'features.enable_request_compression=false', 'model_providers.fixture.name="Local fixture"',
                        'model_providers.fixture.wire_api="responses"', 'model_providers.fixture.requires_openai_auth=false',
                        'model_providers.fixture.supports_websockets=false',
                        f'model_providers.fixture.base_url="http://127.0.0.1:{server.server_port}/v1"']:
            command += ['-c', setting]
        command += ['app-server', '--listen', 'stdio://']
        client = Client(command, workspace,
                        dict(env, CODEX_HOME=str(native_home)), 'deny', str(root / 'unused'))
        record = {'phase': phase, 'command': command, 'native_home': str(native_home), 'expected_path': str(path), 'expected_enabled': enabled}
        result['discoveries'].append(record)
        try:
            deadline = time.monotonic() + 25
            client.response(client.request('initialize', {'clientInfo': {'name': 'oda-skill-controls', 'version': '0'},
                                                           'capabilities': {'experimentalApi': True}}), deadline)
            client.send({'method': 'initialized'})
            record['effective_config'] = client.response(client.request('config/read', {'cwd': str(workspace), 'includeLayers': True}), deadline)
            record['response'] = client.response(client.request('skills/list', {'cwds': [str(workspace)], 'forceReload': True}), deadline)
            skills = [s for row in record['response']['data'] for s in row['skills'] if s['path'] == str(path)]
            assert len(skills) == 1, 'native skill target was not discovered'
            record['observed_skill'] = skills[0]
            record['discovery_matches_setting'] = skills[0]['enabled'] is enabled
            start = len(requests)
            thread = client.response(client.request('thread/start', {'cwd': str(workspace), 'ephemeral': True}), deadline)
            record['thread_id'] = thread['thread']['id']
            client.response(client.request('turn/start', {'threadId': record['thread_id'], 'input': [{'type': 'text', 'text': 'Say fixture complete.'}]}), deadline)
            while True:
                event = client.receive(deadline)
                if event.get('method') == 'turn/completed':
                    record['completed_turn'] = event['params']['turn']
                    break
            record['model_requests'] = requests[start:]
            assert record['completed_turn']['status'] == 'completed' and record['model_requests'], 'native model turn did not complete'
            record['catalog_contains_skill'] = 'oda-skill-reference' in json.dumps(record['model_requests'][0]['input'])
            record['catalog_matches_setting'] = record['catalog_contains_skill'] is enabled
            if args.scope == 'user':
                assert record['catalog_matches_setting'], 'model catalog does not follow skill enablement'
                assert record['discovery_matches_setting'], 'user skill discovery did not follow enablement'
            else:
                assert record['catalog_contains_skill'] and skills[0]['enabled'], 'native project-selector limitation changed'
                assert record['effective_config']['config']['skills']['config'][0]['enabled'] is enabled, 'project config was not loaded'
        finally:
            client.close()
            record.update(events=client.events, stderr=client.errors)

    try:
        invoke(['go', 'build', '-o', str(cli), './cmd/agents'], cwd=repo / 'CLI')
        if args.scope == 'user':
            source_skill = source / 'skills/fixture/SKILL.md'
            source_skill.parent.mkdir(parents=True)
            source_skill.write_text(skill_text)
            source_config = source / 'config.toml'
            source_config.write_text('[[skills.config]]\npath = ' + json.dumps(str(source_skill)) + '\nenabled = false\n')
            source_config.chmod(0o640)
            discover(source, source_skill, False, 'source-disabled')
            before = {p.name: {'sha256': sha(p), 'mode': p.stat().st_mode & 0o777} for p in (source_skill, source_config)}
            adapter('import', canonical, source)
            after = {p.name: {'sha256': sha(p), 'mode': p.stat().st_mode & 0o777} for p in (source_skill, source_config)}
            assert before == after, 'import modified native source'
            result.update(source_before=before, source_after=after)
            projected_skill = home / 'skills/fixture/SKILL.md'
            selector = 'skills/fixture/SKILL.md'
            config = canonical / '.agents/native/com.openai.codex/config.toml'
            imported = tomllib.loads(config.read_text())
            assert imported['skills']['config'] == [{'path': selector, 'enabled': False}], 'copied skill retained source-home selector'
            result['imported_preferences'] = imported
            native_config = home / 'config.toml'
        else:
            native_config = workspace / '.codex/config.toml'
            directory = canonical / '.agents/native/com.openai.codex'
            directory.mkdir(parents=True)
            (canonical / '.agents/manifest.json').write_text(json.dumps({'version': '1.1.0-draft.2', 'profiles': ['skills', 'native']}))
            (canonical / '.agents/AGENTS.md').write_text('Use the isolated fixture.\n')
            (directory / 'profile.json').write_text(json.dumps({'namespace': 'com.openai.codex', 'harness_version': '=0.154.0',
                                                                'scope': 'project', 'required': True,
                                                                'artifacts': [{'kind': 'config', 'source': 'config.toml'}]}))
            projected_skill = canonical / '.agents/skills/fixture/SKILL.md'
            projected_skill.parent.mkdir(parents=True)
            projected_skill.write_text(skill_text)
            selector = '../.agents/skills/fixture/SKILL.md'
            if args.project_selector == 'absolute': selector = str(projected_skill)
            config = directory / 'config.toml'
            config.write_text('[[skills.config]]\npath = ' + json.dumps(selector) + '\nenabled = false\n')
            # Isolated native trust is a fixture prerequisite, not adapter output.
            (home / 'config.toml').write_text('[projects.' + json.dumps(str(workspace)) + ']\ntrust_level = "trusted"\n')
        source_config = config.read_text()
        original_user_config = (home / 'config.toml').read_bytes() if (home / 'config.toml').exists() else None
        if args.scope == 'user':
            adapter('apply', canonical, home)
        else:
            refusal = invoke([cli, 'apply', '--vendor', 'codex', '--root', canonical, '--scope', 'project', '--experimental'], check=False)
            assert refusal['exit_code'] != 0 and 'ignores project skill selectors' in refusal['stderr'], 'required ignored selector did not refuse'
            assert not native_config.exists(), 'refusal wrote project config'
            result['project_refusal'] = refusal
            # Direct native control after adapter refusal; this is fixture setup,
            # not successful adapter projection or authorization to edit trust.
            native_config.parent.mkdir(exist_ok=True)
            native_config.write_text(config.read_text())
            result['project_control_written_by_fixture'] = True
        assert projected_skill.read_text() == skill_text, 'projected skill changed'
        assert tomllib.loads(native_config.read_text())['skills']['config'] == [{'path': selector, 'enabled': False}], 'projection changed selector'
        if args.scope == 'user': assert native_config.stat().st_mode & 0o777 == 0o600, 'new user config is not private'
        discover(home, projected_skill, False, 'applied-disabled')
        config.write_text('[[skills.config]]\npath = ' + json.dumps(selector) + '\nenabled = true\n')
        if args.scope == 'user': adapter('apply', canonical, home)
        else: native_config.write_text(config.read_text())
        discover(home, projected_skill, True, 'applied-enabled')
        config.write_text(source_config)
        if args.scope == 'user': adapter('apply', canonical, home)
        else: native_config.write_text(config.read_text())
        discover(home, projected_skill, False, 'disabled-again')
        if args.scope == 'user':
            again = root / 'reimport'
            adapter('import', again, home)
            result['reimported_preferences'] = tomllib.loads((again / '.agents/native/com.openai.codex/config.toml').read_text())
            assert result['reimported_preferences'] == result['imported_preferences'], 'reimport changed owned references'
            assert not (again / '.agents/skills/.system').exists(), 'system skills were imported'
        else:
            assert (home / 'config.toml').read_bytes() == original_user_config, 'project apply changed user config'
            result['user_config_unchanged'] = True
        result['passed'] = True
    except Exception as error:
        result.update(passed=False, error=str(error), traceback=traceback.format_exc())
    finally:
        server.shutdown()
        server.server_close()
        snapshot.write_bytes(Path(__file__).read_bytes())
        output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'passed': result['passed'], 'output': str(output), 'error': result.get('error')}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
