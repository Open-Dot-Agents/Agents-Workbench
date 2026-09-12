#!/usr/bin/env python3
"""Check Git marketplace fetching with a generated package and loopback Git HTTP.

This fixture does not contact a published marketplace. It records HTTP requests,
resolved commits, package hashes, native discovery, and import preservation.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import tomllib
import traceback
from urllib.parse import urlsplit

from run_native_approvals import Client, PINS, sha
from run_native_codex_hooks import toml


class GitHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.serve()

    def do_POST(self):
        self.serve()

    def serve(self):
        request = urlsplit(self.path)
        env = dict(self.server.fixture_env, GIT_PROJECT_ROOT=str(self.server.git_root),
                   GIT_HTTP_EXPORT_ALL='1', PATH_INFO=request.path,
                   REQUEST_METHOD=self.command, QUERY_STRING=request.query,
                   CONTENT_TYPE=self.headers.get('Content-Type', ''),
                   REMOTE_ADDR='127.0.0.1', HTTP_GIT_PROTOCOL=self.headers.get('Git-Protocol', ''))
        length = int(self.headers.get('Content-Length', '0'))
        data = self.rfile.read(length)
        run = subprocess.run(['git', 'http-backend'], input=data, capture_output=True, env=env, timeout=20)
        header, body = run.stdout.split(b'\r\n\r\n', 1)
        fields = dict(line.decode().split(': ', 1) for line in header.split(b'\r\n'))
        status = int(fields.pop('Status', '200').split()[0])
        self.server.events.append({'method': self.command, 'path': self.path, 'status': status,
                                   'request_bytes': len(data), 'response_bytes': len(body),
                                   'backend_exit_code': run.returncode, 'stderr': run.stderr.decode()})
        self.send_response(status)
        for key, value in fields.items():
            self.send_header(key, value)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vendor', choices=['codex', 'copilot'], required=True)
    parser.add_argument('--scope', choices=['project', 'user'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    snapshot = output.with_suffix('.runner.py')
    assert not output.exists() and not snapshot.exists(), 'refuse evidence replacement'
    binary = Path(shutil.which(args.vendor))
    assert sha(binary) == PINS[args.vendor], 'native pin mismatch'
    repo = Path(__file__).resolve().parents[2]
    root = Path(tempfile.mkdtemp(prefix='oda-plugin-git-'))
    home, workspace, origin, git_root = [root / name for name in ['home', 'workspace', 'origin', 'git']]
    for path in [home, workspace, origin, git_root]:
        path.mkdir(mode=0o700)
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'CODEX_HOME': str(home),
           'COPILOT_HOME': str(home), 'COPILOT_CACHE_HOME': str(root / 'cache'),
           'XDG_STATE_HOME': str(root / 'state'), 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_TERMINAL_PROMPT': '0'}
    result = {'vendor': args.vendor, 'scope': args.scope, 'fixture': str(root),
              'native_version': '0.154.0' if args.vendor == 'codex' else '1.0.83',
              'native_sha256': sha(binary), 'runner_sha256': sha(__file__),
              'implementation_sha256': {str(p.relative_to(repo)): sha(p)
                  for p in sorted((repo / 'CLI/internal/config').glob('*.go'))},
              'helper_sha256': {name: sha(Path(__file__).with_name(name)) for name in
                                ['run_native_approvals.py', 'run_native_codex_hooks.py']},
              'commands': [], 'http_events': [], 'public_marketplace': False,
              'external_model': False, 'copied_credentials': False, 'full_adapter_support': False}

    def invoke(command, cwd=workspace, check=True):
        try:
            # Build with the existing Go cache. Only native fixture commands use
            # the isolated HOME and stores.
            run = subprocess.run(command, cwd=cwd, env=None if command[0] == 'go' else env,
                                 capture_output=True, text=True, timeout=45)
        except subprocess.TimeoutExpired as error:
            result['commands'].append({'command': [str(p) for p in command], 'cwd': str(cwd),
                'timed_out': True, 'stdout': str(error.stdout or ''), 'stderr': str(error.stderr or '')})
            raise
        item = {'command': [str(p) for p in command], 'cwd': str(cwd), 'exit_code': run.returncode,
                'stdout': run.stdout, 'stderr': run.stderr}
        result['commands'].append(item)
        if check:
            assert run.returncode == 0, json.dumps(item)
        return item

    def git(*arguments, cwd=origin):
        return invoke(['git', *arguments], cwd)['stdout'].strip()

    server = ThreadingHTTPServer(('127.0.0.1', 0), GitHandler)
    server.fixture_env, server.git_root, server.events = env, git_root, result['http_events']
    threading.Thread(target=server.serve_forever, daemon=True).start()
    source_url = f'http://127.0.0.1:{server.server_port}/market.git'
    namespace = 'com.openai.codex' if args.vendor == 'codex' else 'com.github.copilot'
    filename = 'config.toml' if args.vendor == 'codex' else 'settings.json'
    directory = '.codex' if args.vendor == 'codex' else '.github/copilot'
    cli = root / 'agents'

    def encode(value):
        return json.dumps(value) if args.vendor == 'copilot' else '\n'.join(json.dumps(k) + ' = ' + toml(v) for k, v in value.items()) + '\n'

    def adapter(operation, target=workspace):
        command = [str(cli), operation, '--vendor', args.vendor, '--root', str(target),
                   '--scope', args.scope, '--experimental']
        if args.scope == 'user':
            command += ['--native-home', str(home)]
        return invoke(command)

    def discover():
        if args.vendor == 'copilot':
            return json.loads(invoke([str(binary), 'skill', 'list', '--json'])['stdout'])
        client = Client([str(binary), 'app-server', '--stdio'], workspace, env, 'deny', '')
        try:
            deadline = time.monotonic() + 30
            client.response(client.request('initialize', {'clientInfo': {'name': 'oda-plugin-git', 'version': '1'},
                'capabilities': {'experimentalApi': True}}), deadline)
            client.send({'method': 'initialized', 'params': {}})
            return client.response(client.request('skills/list', {'cwds': [str(workspace)], 'forceReload': True}), deadline)
        finally:
            client.close()
            result.setdefault('native_events', []).append(client.events)

    try:
        git('init', '-q', '-b', 'main')
        git('init', '-q', str(workspace))
        plugin = origin / 'fixture'
        skill = plugin / 'skills/fixture/SKILL.md'
        skill.parent.mkdir(parents=True)
        skill.write_text('---\nname: fixture\ndescription: Use the isolated Git plugin marker.\n---\nODA_GIT_PLUGIN_FIRST\n')
        manifest = {'$schema': 'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json', 'name': 'fixture', 'version': '1.0.0'}
        (plugin / 'plugin.json').write_text(json.dumps(manifest))
        catalog = {'name': 'oda-git-fixture', 'owner': {'name': 'Fixture'}, 'plugins': [{'name': 'fixture', 'source': './fixture'}]}
        for path in [origin / 'marketplace.json', origin / '.agents/plugins/marketplace.json']:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(catalog))
        git('add', '.')
        git('-c', 'user.name=ODA Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'Generated fixture')
        first = git('rev-parse', 'HEAD')
        git('tag', 'fixture-v1')
        first_hashes = {str(p.relative_to(plugin)): sha(p) for p in plugin.rglob('*') if p.is_file()}
        skill.write_text(skill.read_text().replace('FIRST', 'SECOND'))
        manifest['version'] = '1.1.0'
        (plugin / 'plugin.json').write_text(json.dumps(manifest))
        git('add', '.')
        git('-c', 'user.name=ODA Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'Second generated version')
        second = git('rev-parse', 'HEAD')
        second_hashes = {str(p.relative_to(plugin)): sha(p) for p in plugin.rglob('*') if p.is_file()}
        git('clone', '--bare', str(origin), str(git_root / 'market.git'))
        result.update(source_url=source_url, first_revision=first, second_revision=second,
                      first_package_hashes=first_hashes, second_package_hashes=second_hashes)
        invoke(['go', 'build', '-o', str(cli), './cmd/agents'], repo / 'CLI')
        if args.vendor == 'codex':
            (home / filename).write_text('[features]\nplugins=true\nremote_plugin=false\nrecommended_plugins=false\n[projects.' + json.dumps(str(workspace)) + ']\ntrust_level="trusted"\n')
            values = {'plugins': {'fixture@oda-git-fixture': {'enabled': True}},
                      'marketplaces': {'oda-git-fixture': {'source_type': 'git', 'source': source_url, 'ref': 'fixture-v1'}}}
        else:
            (home / 'config.json').write_text(json.dumps({'trustedFolders': [str(workspace)], 'autoUpdate': False}))
            values = {'enabledPlugins': {'fixture@oda-git-fixture': True},
                      'extraKnownMarketplaces': {'oda-git-fixture': {'source': {'source': 'git', 'url': source_url}}}}
        canonical = workspace / '.agents/plugins' / namespace
        canonical.mkdir(parents=True)
        (workspace / '.agents/manifest.json').write_text(json.dumps({'version': '1.1.0-draft.2', 'profiles': ['plugins']}))
        (workspace / '.agents/AGENTS.md').write_text('Use the isolated plugin fixture.\n')
        (canonical / 'profile.json').write_text(json.dumps({'namespace': namespace, 'harness_version': '=' + result['native_version'],
            'scope': args.scope, 'required': True, 'artifacts': [{'kind': 'config', 'source': filename}]}))
        (canonical / filename).write_text(encode(values))
        before = {str(p.relative_to(home)): sha(p) for p in home.rglob('*') if p.is_file()}
        adapter('apply')
        assert not result['http_events'], 'apply fetched remote content'
        if args.scope == 'project':
            assert before == {str(p.relative_to(home)): sha(p) for p in home.rglob('*') if p.is_file()}, 'project apply changed user files'
        result['availability_before_install'] = invoke([str(binary), 'plugin', 'marketplace', 'list'], check=args.vendor != 'codex')
        if args.vendor == 'codex':
            # Selection writes do not create the native marketplace cache.
            result['marketplace_install'] = invoke([str(binary), 'plugin', 'marketplace', 'add', source_url, '--ref', 'fixture-v1', '--json'])
            result['availability'] = invoke([str(binary), 'plugin', 'marketplace', 'list'])
        operation = 'add' if args.vendor == 'codex' else 'install'
        result['install'] = invoke([str(binary), 'plugin', operation, 'fixture@oda-git-fixture'])
        result['discovery'] = discover()
        if args.vendor == 'codex':
            skills = [s for item in result['discovery']['data'] for s in item['skills'] if s.get('pluginId') == 'fixture@oda-git-fixture']
            assert len(skills) == 1 and skills[0]['enabled'] is True
            installed = Path(skills[0]['path']).parents[2]
            expected = first_hashes
        else:
            skills = [s for s in result['discovery'] if s['name'] == 'fixture' and s['source'] == 'plugin']
            assert len(skills) == 1 and skills[0]['enabled'] is True
            installed = Path(skills[0]['path']).parents[1]
            expected = second_hashes
        actual = {name: sha(installed / name) for name in expected}
        assert actual == expected, 'fetched package does not match the selected Git revision'
        assert installed.is_relative_to(home) and not installed.is_relative_to(origin), 'Git package was not installed into the native home'
        assert any(e['method'] == 'POST' and e['status'] == 200 and e['path'].endswith('/git-upload-pack') for e in result['http_events']), 'no correlated Git fetch'
        result.update(installed_path=str(installed), installed_hashes=actual)
        again = root / 'reimport'
        again.mkdir()
        if args.scope == 'project':
            shutil.copytree(workspace / directory, again / directory)
        adapter('import', again)
        parse = tomllib.loads if args.vendor == 'codex' else json.loads
        assert parse((again / '.agents/plugins' / namespace / filename).read_text()) == values, 'Git selection changed during reimport'
        assert not any(p.name in ['plugin.json', 'SKILL.md'] for p in (again / '.agents').rglob('*')), 'import copied package assets'
        if args.vendor == 'codex':
            values['plugins']['fixture@oda-git-fixture']['enabled'] = False
        else:
            values['enabledPlugins']['fixture@oda-git-fixture'] = False
        (canonical / filename).write_text(encode(values))
        requests_before_disable = len(result['http_events'])
        adapter('apply')
        assert len(result['http_events']) == requests_before_disable, 'disable apply fetched remote content'
        result['disabled_discovery'] = discover()
        if args.vendor == 'codex':
            assert not any(s.get('pluginId') == 'fixture@oda-git-fixture' for item in result['disabled_discovery']['data'] for s in item['skills']), 'disabled Git plugin skill is still active'
        else:
            assert not any(s['name'] == 'fixture' and s['source'] == 'plugin' for s in result['disabled_discovery']), 'disabled Git plugin skill is still active'
        assert {name: sha(installed / name) for name in expected} == expected, 'disable changed installed package bytes'
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
