#!/usr/bin/env python3
"""Compare read-only legacy import with pinned Copilot's native migration."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import traceback

from run_native_approvals import PINS, sha


def parse(path):
    # Copilot's generated state file has whole-line // comments. Preserve JSON
    # string content, including URLs; this is not a general JSONC parser.
    return json.loads('\n'.join(line for line in path.read_text().splitlines() if not line.lstrip().startswith('//')))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=['missing', 'conflict', 'nested'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    snapshot = output.with_suffix('.runner.py')
    assert not output.exists() and not snapshot.exists(), 'refuse evidence replacement'
    binary = Path(shutil.which('copilot'))
    assert sha(binary) == PINS['copilot'], 'native pin mismatch'
    repo = Path(__file__).resolve().parents[2]
    root = Path(tempfile.mkdtemp(prefix='oda-native-legacy-'))
    source, control, target, workspace = [root / name for name in ['source', 'control', 'target', 'workspace']]
    for path in [source, control, target, workspace]: path.mkdir(mode=0o700)
    subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
    state = {'trustedFolders': [str(workspace)], 'firstLaunchAt': 1234, 'staff': False}
    preferences = {'theme': 'dim', 'memory': False, 'bannerStyle': 'classic',
                   'ide': {'autoConnect': False}, 'companyAnnouncements': ['first', 'second']}
    legacy = dict(state, **preferences)
    modern = None
    if args.case == 'conflict': modern = {'theme': 'github', 'memory': True, 'companyAnnouncements': ['modern'], 'beep': False}
    if args.case == 'nested': modern = {'ide': {'openDiffOnEdit': True}, 'tabs': {'enabled': False, 'sort': ['agents', 'copilot']}}
    for home in [source, control]:
        (home / 'config.json').write_text('// Generated legacy fixture.\n' + json.dumps(legacy))
        (home / 'config.json').chmod(0o640)
        if modern is not None: (home / 'settings.json').write_text(json.dumps(modern))
    (target / 'config.json').write_text(json.dumps(state))
    (target / 'config.json').chmod(0o600)
    cli = root / 'agents'
    result = {'case': args.case, 'fixture': str(root), 'native_version': '1.0.83', 'native_sha256': sha(binary),
              'runner_sha256': sha(__file__), 'helper_sha256': {'run_native_approvals.py': sha(Path(__file__).with_name('run_native_approvals.py'))},
              'implementation_sha256': {str(p.relative_to(repo)): sha(p) for p in sorted((repo / 'CLI/internal/config').glob('*.go'))},
              'initial_legacy': legacy, 'initial_modern': modern, 'commands': [], 'full_adapter_support': False,
              'native_behavior_scope': 'preference migration and configuration preservation; no memory-service or UI rendering claim'}

    def invoke(command, home=source, cwd=workspace, check=True):
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'COPILOT_HOME': str(home), 'COPILOT_CACHE_HOME': str(home / 'cache'),
               'COPILOT_OFFLINE': 'true', 'XDG_STATE_HOME': str(root / 'state')}
        run = subprocess.run(command, cwd=cwd, env=None if command[0] == 'go' else env, capture_output=True, text=True, timeout=30)
        record = {'command': [str(p) for p in command], 'native_home': str(home), 'exit_code': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr}
        result['commands'].append(record)
        if check: assert run.returncode == 0, json.dumps(record)
        return record

    def adapter(operation, canonical, home, *flags, check=True):
        return invoke([str(cli), operation, '--vendor', 'copilot', '--root', str(canonical), '--scope', 'user',
                       '--native-home', str(home), '--experimental', *flags], check=check)

    try:
        invoke(['go', 'build', '-o', str(cli), './cmd/agents'], cwd=repo / 'CLI')
        # Native migration runs only in the independent control home.
        invoke([str(binary), 'skill', 'list', '--json'], home=control)
        effective = parse(control / 'settings.json')
        assert all(effective[key] == value for key, value in preferences.items()), 'legacy precedence changed'
        assert parse(control / 'config.json') == state, 'native did not leave application state separate'
        result['native_migrated_preferences'] = effective
        before = {p.name: {'sha256': sha(p), 'mode': p.stat().st_mode & 0o777} for p in source.iterdir() if p.is_file()}
        adapter('import', workspace, source)
        after = {p.name: {'sha256': sha(p), 'mode': p.stat().st_mode & 0o777} for p in source.iterdir() if p.is_file()}
        assert before == after, 'import mutated legacy/native source files'
        canonical = workspace / '.agents/native/com.github.copilot/settings.json'
        assert parse(canonical) == effective, 'import differs from effective native migration'
        state_before = sha(target / 'config.json')
        adapter('apply', workspace, target)
        assert sha(target / 'config.json') == state_before, 'apply changed native state'
        assert parse(target / 'settings.json') == effective, 'projected preferences differ'
        assert target.joinpath('settings.json').stat().st_mode & 0o777 == 0o600, 'new user preferences are not private'
        invoke([str(binary), 'skill', 'list', '--json'], home=target)
        assert parse(target / 'settings.json') == effective, 'native startup changed projected preferences'
        again = root / 'reimport'
        adapter('import', again, target)
        assert parse(again / '.agents/native/com.github.copilot/settings.json') == effective, 'reimport lost preferences'
        # A pending migration with a conflicting root must not silently undo a
        # new adapter-owned value at the next native startup.
        pending = root / 'pending'
        pending.mkdir()
        (pending / 'config.json').write_text(json.dumps({'memory': True, **state}))
        pending_before = sha(pending / 'config.json')
        refusal = adapter('apply', workspace, pending, '--force', '--adopt', '--backup', check=False)
        assert refusal['exit_code'] != 0 and 'would replace setting memory' in refusal['stderr'], 'pending migration did not block conflicting apply'
        assert not (pending / 'settings.json').exists() and sha(pending / 'config.json') == pending_before, 'refused apply changed pending state'
        result.update(passed=True, source_before=before, source_after=after, imported_preferences=parse(canonical),
                      projected_preferences=parse(target / 'settings.json'), state_sha256_after_apply=state_before,
                      pending_migration_refusal=refusal)
    except Exception as error:
        result.update(passed=False, error=str(error), traceback=traceback.format_exc())
    finally:
        snapshot.write_bytes(Path(__file__).read_bytes())
        output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'passed': result['passed'], 'output': str(output), 'error': result.get('error')}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
