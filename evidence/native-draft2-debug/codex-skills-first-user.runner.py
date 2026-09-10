#!/usr/bin/env python3
"""Measure projected Codex skill controls and imported path relocation."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import time
import tomllib
import traceback

from run_native_approvals import Client, PINS, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope', choices=['project', 'user'], required=True)
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
    result = {'scope': args.scope, 'fixture': str(root), 'native_version': '0.154.0', 'native_sha256': sha(binary),
              'runner_sha256': sha(__file__), 'helper_sha256': {'run_native_approvals.py': sha(Path(__file__).with_name('run_native_approvals.py'))},
              'implementation_sha256': {str(p.relative_to(repo)): sha(p) for p in sorted((repo / 'CLI/internal/config').glob('*.go'))},
              'commands': [], 'discoveries': [], 'full_adapter_support': False,
              'limitations': ['Discovery and enablement only; no model turn or skill-script execution.',
                              'Native HOME expressions and references outside copied packages remain external.',
                              'No system-package ownership or source-home state import.']}

    def invoke(command, cwd=workspace):
        run = subprocess.run([str(p) for p in command], cwd=cwd, env=None if command[0] == 'go' else env,
                             capture_output=True, text=True, timeout=90)
        record = {'command': [str(p) for p in command], 'exit_code': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr}
        result['commands'].append(record)
        assert run.returncode == 0, json.dumps(record)

    def adapter(operation, owner, native_home):
        command = [cli, operation, '--vendor', 'codex', '--root', owner, '--scope', args.scope, '--experimental']
        if args.scope == 'user': command += ['--native-home', native_home]
        invoke(command)

    def discover(native_home, path, enabled, phase):
        client = Client([str(binary), 'app-server', '--listen', 'stdio://'], workspace,
                        dict(env, CODEX_HOME=str(native_home)), 'deny', str(root / 'unused'))
        record = {'phase': phase, 'native_home': str(native_home), 'expected_path': str(path), 'expected_enabled': enabled}
        result['discoveries'].append(record)
        try:
            deadline = time.monotonic() + 25
            client.response(client.request('initialize', {'clientInfo': {'name': 'oda-skill-controls', 'version': '0'},
                                                           'capabilities': {'experimentalApi': True}}), deadline)
            client.send({'method': 'initialized'})
            record['response'] = client.response(client.request('skills/list', {'cwds': [str(workspace)], 'forceReload': True}), deadline)
            skills = [s for row in record['response']['data'] for s in row['skills'] if s['path'] == str(path)]
            assert len(skills) == 1 and skills[0]['enabled'] is enabled, 'native skill enablement changed or target was not discovered'
            record['observed_skill'] = skills[0]
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
            config = directory / 'config.toml'
            config.write_text('[[skills.config]]\npath = ' + json.dumps(selector) + '\nenabled = false\n')
            # Isolated native trust is a fixture prerequisite, not adapter output.
            (home / 'config.toml').write_text('[projects.' + json.dumps(str(workspace)) + ']\ntrust_level = "trusted"\n')
        source_config = config.read_text()
        original_user_config = (home / 'config.toml').read_bytes() if (home / 'config.toml').exists() else None
        adapter('apply', canonical, home)
        assert projected_skill.read_text() == skill_text, 'projected skill changed'
        assert tomllib.loads(native_config.read_text())['skills']['config'] == [{'path': selector, 'enabled': False}], 'projection changed selector'
        if args.scope == 'user': assert native_config.stat().st_mode & 0o777 == 0o600, 'new user config is not private'
        discover(home, projected_skill, False, 'applied-disabled')
        config.write_text('[[skills.config]]\npath = ' + json.dumps(selector) + '\nenabled = true\n')
        adapter('apply', canonical, home)
        discover(home, projected_skill, True, 'applied-enabled')
        config.write_text(source_config)
        adapter('apply', canonical, home)
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
        snapshot.write_bytes(Path(__file__).read_bytes())
        output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'passed': result['passed'], 'output': str(output), 'error': result.get('error')}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
