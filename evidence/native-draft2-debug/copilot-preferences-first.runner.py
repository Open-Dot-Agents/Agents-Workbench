#!/usr/bin/env python3
"""Apply user preferences and measure pinned Copilot terminal behavior."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import traceback

import pexpect

from run_native_approvals import PINS, sha


def plain_terminal(raw):
    raw = re.sub(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)', '', raw)
    return re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    snapshot = output.with_suffix('.runner.py')
    assert not output.exists() and not snapshot.exists(), 'refuse evidence replacement'
    binary = Path(shutil.which('copilot'))
    assert sha(binary) == PINS['copilot'], 'native pin mismatch'
    repo = Path(__file__).resolve().parents[2]
    root = Path(tempfile.mkdtemp(prefix='oda-native-preferences-', dir='/mnt/DATA/tmp'))
    home, workspace = root / 'home', root / 'workspace'
    for path in (home, workspace):
        path.mkdir(mode=0o700)
    subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
    # Trust is isolated native fixture setup. The adapter must not write it.
    state_path = home / 'config.json'
    state_path.write_text(json.dumps({'trustedFolders': [str(workspace)], 'firstLaunchAt': 1234}))
    state_path.chmod(0o600)
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'COPILOT_HOME': str(home),
           'COPILOT_CACHE_HOME': str(root / 'cache'), 'XDG_STATE_HOME': str(root / 'state'),
           'COPILOT_OFFLINE': 'true', 'COPILOT_PROVIDER_TYPE': 'openai',
           'COPILOT_PROVIDER_WIRE_API': 'completions',
           'COPILOT_PROVIDER_BASE_URL': 'http://127.0.0.1:9/v1',
           'COPILOT_MODEL': 'fixture-model', 'TERM': 'xterm-256color'}
    cli = root / 'agents'
    result = {'fixture': str(root), 'native_version': '1.0.83', 'native_sha256': sha(binary),
              'runner_sha256': sha(__file__),
              'helper_sha256': {'run_native_approvals.py': sha(Path(__file__).with_name('run_native_approvals.py'))},
              'implementation_sha256': {str(p.relative_to(repo)): sha(p) for p in sorted((repo / 'CLI/internal/config').glob('*.go'))},
              'commands': [], 'phases': [], 'full_adapter_support': False,
              'limitations': ['No model turn, autopilot execution, notifications, or image rendering test.',
                              'Terminal output establishes initial mode; no session.mode_changed event is claimed.',
                              'Timer checks allow scheduling delay and do not establish exact timing guarantees.']}

    def invoke(command, cwd=workspace):
        run = subprocess.run([str(p) for p in command], cwd=cwd,
                             env=None if command[0] == 'go' else env,
                             capture_output=True, text=True, timeout=90)
        record = {'command': [str(p) for p in command], 'exit_code': run.returncode,
                  'stdout': run.stdout, 'stderr': run.stderr}
        result['commands'].append(record)
        assert run.returncode == 0, json.dumps(record)
        return record

    def adapter(operation, canonical):
        return invoke([cli, operation, '--vendor', 'copilot', '--root', canonical,
                       '--scope', 'user', '--native-home', home, '--experimental'])

    def terminal(phase):
        command = [str(binary), '--no-auto-update', '--no-remote', '--disable-builtin-mcps']
        phase['command'] = command
        phase['dimensions'] = [40, 140]
        child = pexpect.spawn(command[0], command[1:], cwd=str(workspace), env=env,
                              encoding='utf-8', dimensions=(40, 140), timeout=1)
        raw, pending = '', ''
        start = time.monotonic()
        try:
            while time.monotonic() - start < 10 and child.isalive():
                try:
                    chunk = child.read_nonblocking(32768, timeout=.25)
                except pexpect.TIMEOUT:
                    continue
                except pexpect.EOF:
                    break
                raw += chunk
                pending += chunk
                for query, reply in [('\x1b[6n', '\x1b[1;1R'),
                                     ('\x1b]11;?', '\x1b]11;rgb:0000/0000/0000\x1b\\')]:
                    if query in pending:
                        child.send(reply)
                        pending = pending.replace(query, '')
                pending = pending[-16:]
            phase['alive_before_teardown'] = child.isalive()
        finally:
            child.close(force=True)
            phase.update(raw_output=raw, output=plain_terminal(raw),
                         elapsed_seconds=time.monotonic() - start,
                         exit_status=child.exitstatus, signal_status=child.signalstatus,
                         teardown='pexpect close(force=True) after the observation window')
        assert phase['alive_before_teardown'], 'native exited before the observation window ended'

    try:
        invoke(['go', 'build', '-o', str(cli), './cmd/agents'], cwd=repo / 'CLI')
        canonical_dir = workspace / '.agents/native/com.github.copilot'
        canonical_dir.mkdir(parents=True)
        (workspace / '.agents/manifest.json').write_text(json.dumps({'version': '1.1.0-draft.2', 'profiles': ['native']}))
        (workspace / '.agents/AGENTS.md').write_text('Use this isolated fixture.\n')
        (canonical_dir / 'profile.json').write_text(json.dumps({
            'namespace': 'com.github.copilot', 'harness_version': '=1.0.83', 'scope': 'user',
            'required': True, 'artifacts': [{'kind': 'config', 'source': 'settings.json'}]}))
        preferences_path = canonical_dir / 'settings.json'
        event_path = root / 'status-events.jsonl'
        script = root / 'status.py'
        script.write_text('import json,sys,time\nfrom pathlib import Path\n'
                          'value=json.load(sys.stdin)\n'
                          f'with Path({str(event_path)!r}).open("a") as stream:\n'
                          ' stream.write(json.dumps({"recorded_at":time.monotonic(),"phase":sys.argv[1],"value":value})+"\\n")\n'
                          'print("ODA_STATUS_"+sys.argv[1])\n')
        # The same repository owns each update and removal. The second session
        # uses the same native home but starts a fresh interactive session.
        for name, mode, tabs, interval, padding in [
            ('plan', 'plan', {'enabled': True, 'sort': ['AGENTS', 'CoPiLoT'], 'hide': ['GISTS']}, 1, 3),
            ('interactive', 'interactive', {'enabled': True, 'sort': ['copilot', 'agents'], 'hide': []}, 2, 0),
            ('removed', 'interactive', {'enabled': False}, None, None),
        ]:
            values = {'defaultMode': mode, 'banner': 'never', 'showTipsOnStartup': False,
                      'memory': False, 'tabs': tabs}
            if interval is not None:
                values['statusLine'] = {'command': f'/usr/bin/python3 {script} {name}',
                                        'refreshInterval': interval, 'padding': padding}
                if name == 'plan':
                    values['statusLine']['type'] = 'command'
            preferences_path.write_text(json.dumps(values))
            phase = {'name': name, 'preferences': values}
            result['phases'].append(phase)
            before = sha(state_path)
            adapter('apply', workspace)
            phase['state_unchanged_by_apply'] = sha(state_path) == before
            assert phase['state_unchanged_by_apply'], 'apply wrote native state'
            assert not event_path.exists() or all(json.loads(line)['phase'] != name for line in event_path.read_text().splitlines()), 'apply executed the status command'
            native_settings = home / 'settings.json'
            phase['projected_preferences'] = json.loads(native_settings.read_text())
            phase['settings_mode'] = native_settings.stat().st_mode & 0o777
            assert phase['projected_preferences'] == values, 'projection changed preferences'
            assert phase['settings_mode'] == 0o600, 'new user preferences are not private'
            before_settings = sha(native_settings)
            before_events = event_path.read_bytes() if event_path.exists() else b''
            terminal(phase)
            phase['settings_unchanged_by_native'] = sha(native_settings) == before_settings
            assert phase['settings_unchanged_by_native'], 'native changed projected preferences'
            events = [json.loads(line) for line in event_path.read_text().splitlines()] if event_path.exists() else []
            phase['status_events'] = [e for e in events if e['phase'] == name]
            if interval is not None:
                assert len(phase['status_events']) >= 3, 'status timer did not repeat'
                session_ids = {e['value']['session_id'] for e in phase['status_events']}
                assert len(session_ids) == 1, 'status calls came from different sessions'
                assert all(e['value']['cwd'] == str(workspace) and e['value']['version'] == '1.0.83'
                           and e['value']['model']['id'] == 'fixture-model' for e in phase['status_events']), 'native status input mismatch'
                times = [e['recorded_at'] for e in phase['status_events']]
                phase['refresh_gaps'] = [b - a for a, b in zip(times, times[1:])]
                # Startup can produce event-driven calls. Require two later gaps
                # close to the selected interval, with generous scheduler slack.
                assert sum(interval * .65 <= gap <= interval * 1.5 for gap in phase['refresh_gaps']) >= 2, 'selected refresh interval not observed'
                assert re.search(r'\x1b\[\d+;' + str(padding + 1) + 'HODA_STATUS_' + name, phase['raw_output']), 'status output/padding not observed'
            else:
                assert event_path.read_bytes() == before_events, 'removed status command still executed'
                assert 'ODA_STATUS_' not in phase['output'], 'removed status line still rendered'
            if name == 'plan':
                assert ' · plan · ' in phase['output'], 'plan mode indicator missing'
                assert re.search(r'Sessions\s+\[Current\]\s+Issues\s+Pull requests', phase['output']), 'selected tab order not observed'
                assert 'Gists' not in phase['output'], 'hidden Gists tab rendered'
            elif name == 'interactive':
                assert ' · plan · ' not in phase['output'], 'interactive mode still shows plan indicator'
                assert re.search(r'\[Current\]\s+Sessions\s+Issues\s+Pull requests\s+Gists', phase['output']), 'default tab order/restored Gists not observed'
            else:
                assert '[Current]' not in phase['output'] and 'tab next tab' not in phase['output'], 'disabled tabs still rendered'
            again = root / ('reimport-' + name)
            source_before = sha(native_settings)
            adapter('import', again)
            phase['reimported_preferences'] = json.loads((again / '.agents/native/com.github.copilot/settings.json').read_text())
            assert phase['reimported_preferences'] == values, 'reimport changed preferences'
            assert sha(native_settings) == source_before, 'import wrote source preferences'
        first, second = [p['status_events'][0]['value']['session_id'] for p in result['phases'][:2]]
        assert first != second, 'control did not start a fresh session'
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
