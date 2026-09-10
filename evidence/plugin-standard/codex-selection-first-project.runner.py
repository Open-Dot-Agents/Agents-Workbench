#!/usr/bin/env python3
"""Verify plugin selection import, projection, installation boundaries, and discovery.

Native installation is explicit fixture setup, using only a generated local
marketplace. No published package, user store, credential, or model is used.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import tomllib

from run_native_approvals import Client, PINS, sha
from run_native_codex_hooks import toml


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
    root = Path(tempfile.mkdtemp(prefix='oda-plugin-selections-', dir='/mnt/DATA/tmp'))
    home, workspace, source, market = [root / name for name in ['home', 'workspace', 'source', 'market']]
    for path in [home, workspace, source, market]:
        path.mkdir(mode=0o700)
    for path in [workspace, source]:
        subprocess.run(['git', 'init', '-q', str(path)], check=True)
    plugin = market / 'fixture'
    skill = plugin / 'skills/fixture/SKILL.md'
    skill.parent.mkdir(parents=True)
    skill.write_text('---\nname: fixture\ndescription: Use the isolated plugin fixture marker.\n---\nODA_PLUGIN_SKILL_CONTEXT\n')
    manifest = {'$schema': 'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json',
                'name': 'fixture', 'version': '1.0.0'}
    (plugin / 'plugin.json').write_text(json.dumps(manifest))
    catalog = {'name': 'oda-fixture', 'owner': {'name': 'Fixture'},
               'plugins': [{'name': 'fixture', 'source': './fixture'}]}
    (market / 'marketplace.json').write_text(json.dumps(catalog))
    (market / '.agents/plugins').mkdir(parents=True)
    (market / '.agents/plugins/marketplace.json').write_text(json.dumps(catalog))
    package_hashes = {str(p.relative_to(plugin)): sha(p) for p in plugin.rglob('*') if p.is_file()}
    native_policy = ('[features]\nplugins=true\nremote_plugin=false\nrecommended_plugins=false\n'
                     '[projects.' + json.dumps(str(workspace)) + ']\ntrust_level="trusted"\n')
    if args.vendor == 'codex':
        (home / 'config.toml').write_text(native_policy)
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'CODEX_HOME': str(home),
           'COPILOT_HOME': str(home), 'COPILOT_CACHE_HOME': str(root / 'cache'),
           'XDG_STATE_HOME': str(root / 'state')}
    namespace = 'com.openai.codex' if args.vendor == 'codex' else 'com.github.copilot'
    filename = 'config.toml' if args.vendor == 'codex' else 'settings.json'
    directory = '.codex' if args.vendor == 'codex' else '.github/copilot'
    cli = root / 'agents'
    subprocess.run(['go', 'build', '-o', str(cli), './cmd/agents'], cwd=repo / 'CLI', check=True)
    result = {'fixture': str(root), 'vendor': args.vendor, 'scope': args.scope,
              'native_version': '0.154.0' if args.vendor == 'codex' else '1.0.83',
              'native_sha256': sha(binary), 'runner_sha256': sha(__file__),
              'helper_sha256': {name: sha(Path(__file__).with_name(name)) for name in
                                ['run_native_approvals.py', 'run_native_codex_hooks.py']},
              'implementation_sha256': {str(p.relative_to(repo)): sha(p)
                  for p in sorted((repo / 'CLI/internal/config').glob('*.go'))},
              'package_hashes': package_hashes, 'package_manifest': manifest,
              'package_installation': 'explicit native fixture action after adapter apply',
              'external_model': False, 'copied_credentials': False,
              'full_adapter_support': False, 'commands': []}

    def configuration(enabled):
        if args.vendor == 'codex':
            return {'plugins': {'fixture@oda-fixture': {'enabled': enabled}},
                    'marketplaces': {'oda-fixture': {'source_type': 'local', 'source': str(market)}}}
        return {'enabledPlugins': {'fixture@oda-fixture': enabled},
                'extraKnownMarketplaces': {'oda-fixture': {'source': {'source': 'directory', 'path': str(market)}}}}

    def encode(values):
        if args.vendor == 'copilot':
            return json.dumps(values)
        return '\n'.join(json.dumps(key) + ' = ' + toml(value) for key, value in values.items()) + '\n'

    def invoke(command, cwd=workspace, check=True):
        completed = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=45)
        record = {'command': command, 'cwd': str(cwd), 'exit_code': completed.returncode,
                  'stdout': completed.stdout, 'stderr': completed.stderr}
        result['commands'].append(record)
        if check:
            assert completed.returncode == 0, 'command failed: ' + json.dumps(command)
        return record

    def adapter(operation, target, native_home=None):
        command = [str(cli), operation, '--vendor', args.vendor, '--root', str(target),
                   '--scope', args.scope, '--experimental']
        if native_home:
            command += ['--native-home', str(native_home)]
        return invoke(command)

    def discover():
        if args.vendor == 'copilot':
            completed = invoke([str(binary), 'plugins', 'list', '--json', '--kind', 'plugin,skill'])
            return json.loads(completed['stdout'])
        client = Client([str(binary), 'app-server', '--stdio'], workspace, env, 'deny', '')
        try:
            deadline = time.monotonic() + 30
            client.response(client.request('initialize', {'clientInfo': {'name': 'oda-plugins', 'version': '1'},
                'capabilities': {'experimentalApi': True}}), deadline)
            client.send({'method': 'initialized', 'params': {}})
            response = client.response(client.request('skills/list', {'cwds': [str(workspace)], 'forceReload': True}), deadline)
            return response
        finally:
            client.close()
            result.setdefault('native_events', []).append(client.events)
            result.setdefault('native_stderr', []).append(client.errors)

    try:
        seed = source / (directory if args.scope == 'project' else '') / filename
        seed.parent.mkdir(parents=True, exist_ok=True)
        seed.write_text(encode(configuration(True)))
        adapter('import', source, source if args.scope == 'user' else None)
        shutil.copytree(source / '.agents', workspace / '.agents')
        selection = workspace / '.agents/plugins' / namespace / filename
        assert selection.exists(), 'selection was not imported under .agents/plugins'
        before = {str(p.relative_to(home)): sha(p) for p in home.rglob('*') if p.is_file()}
        adapter('apply', workspace, home if args.scope == 'user' else None)
        after = {str(p.relative_to(home)): sha(p) for p in home.rglob('*') if p.is_file()}
        result.update(user_files_before_apply=before, user_files_after_apply=after)
        assert not (home / 'installed-plugins').exists() and not (home / 'plugins').exists(), 'adapter installed a package'
        if args.scope == 'project':
            assert before == after, 'project apply changed user configuration'
        # Marketplace and install commands are native fixture operations. The
        # source package is generated locally, so no remote package is fetched.
        result['marketplace_list'] = invoke([str(binary), 'plugin', 'marketplace', 'list'])
        assert 'oda-fixture' in result['marketplace_list']['stdout'], 'projected marketplace not recognized'
        install = 'add' if args.vendor == 'codex' else 'install'
        result['install'] = invoke([str(binary), 'plugin', install, 'fixture@oda-fixture'])
        result['enabled_discovery'] = discover()
        assert 'fixture' in json.dumps(result['enabled_discovery']), 'native plugin skill not discovered'
        # Reimport the installed selection. The package files and runtime state
        # stay outside the canonical tree.
        again = root / 'reimport'
        if args.scope == 'project':
            again.mkdir()
            subprocess.run(['git', 'init', '-q', str(again)], check=True)
            shutil.copytree(workspace / directory, again / directory)
        adapter('import', again, home if args.scope == 'user' else None)
        reimported = again / '.agents/plugins' / namespace / filename
        parse = tomllib.loads if args.vendor == 'codex' else json.loads
        assert parse(selection.read_text()) == parse(reimported.read_text()), 'installed selection changed on reimport'
        assert not any(p.name in ['SKILL.md', 'plugin.json'] for p in (again / '.agents').rglob('*')), 'import copied package content'
        selection.write_text(encode(configuration(False)))
        adapter('apply', workspace, home if args.scope == 'user' else None)
        result['disabled_discovery'] = discover()
        assert result['enabled_discovery'] != result['disabled_discovery'], 'plugin disable had no discovery effect'
        assert {str(p.relative_to(plugin)): sha(p) for p in plugin.rglob('*') if p.is_file()} == package_hashes, 'source package changed'
        result['passed'] = True
    except Exception as error:
        result.update(passed=False, error=str(error))
    finally:
        snapshot.write_bytes(Path(__file__).read_bytes())
        output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'output': str(output), 'passed': result['passed'], 'error': result.get('error')}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
