#!/usr/bin/env python3
"""Record draft refusal evidence and optional local native sandbox observations.

No account, model request, package install, native trust write, or remote network
request is required. Native probes use an isolated config and harmless files.
They do not activate the portable policy and cannot establish adapter support.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[2]
PINS = {'codex': '0.154.0', 'copilot': '1.0.83'}
SCENARIOS = [
    'allowed-read', 'denied-read', 'allowed-write', 'denied-write',
    'allowed-execution', 'denied-execution', 'symlink-escape', 'path-traversal',
    'overlapping-grants', 'exact-command-arguments', 'shell-composition',
    'approval-denial', 'noninteractive-ask', 'direct-network', 'redirect-network',
    'private-network', 'web-tool-network', 'remote-mcp-network', 'local-mcp',
    'lsp-process', 'delegation', 'background-process', 'credential-environment',
    'credential-files', 'conflicting-settings', 'missing-prerequisite',
    'bypass-setting', 'profile-removal', 'rollback', 'idempotence',
]


def run(argv, **kwargs):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=45, **kwargs)
        return dict(command=list(map(str, argv)), returncode=result.returncode,
                    stdout=result.stdout, stderr=result.stderr)
    except (OSError, subprocess.TimeoutExpired) as error:
        return dict(command=list(map(str, argv)), returncode=None, error=str(error))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def snapshot(root):
    result = {}
    for path in sorted(root.rglob('*')):
        relative = str(path.relative_to(root))
        if path.is_symlink():
            result[relative] = 'link:' + os.readlink(path)
        elif path.is_dir():
            result[relative] = 'directory'
        else:
            result[relative] = digest(path)
    return result


def version_matches(output, expected):
    match = re.search(r'(?<![0-9.])([0-9]+\.[0-9]+\.[0-9]+)(?![0-9])', output)
    return bool(match and match.group(1) == expected)


def prerequisites():
    names = ['bwrap', 'slirp4netns', 'unshare', 'nsenter', 'iptables', 'ip6tables',
             'iptables-restore', 'ip6tables-restore']
    return dict(platform=platform.platform(), tools={name: shutil.which(name) for name in names},
                tun_access=os.access('/dev/net/tun', os.R_OK | os.W_OK))


def refusal_evidence(agents, output):
    fixture = output / 'portable-fixture'
    shutil.copytree(REPO / 'SPEC/examples/security-draft', fixture)
    # A conflicting native setting and runtime bypass flag cannot force writes.
    (fixture / '.codex').mkdir()
    (fixture / '.codex/config.toml').write_text('sandbox_mode = "danger-full-access"\n')
    before = snapshot(fixture)
    env = dict(os.environ, COPILOT_SANDBOX_ALLOW_UNSANDBOXED_PROCESSES='true')
    rows = []
    for vendor in PINS:
        for command in ['plan', 'apply', 'sync', 'import']:
            args = [str(agents), command, '--experimental', '--root', str(fixture), '--vendor', vendor]
            if command != 'import':
                args += ['--format', 'json']
            if command != 'plan':
                args += ['--force', '--backup']
            record = run(args, env=env)
            record.update(vendor=vendor, case=command + '-refusal', kind='adapter-refusal')
            if command == 'plan':
                try:
                    plan = json.loads(record['stdout'])
                    expected = record['returncode'] == 0 and plan['applicable'] is False and plan['security']['status'] == 'refused' and not plan['actions']
                except (ValueError, KeyError):
                    expected = False
            else:
                expected = record['returncode'] not in (None, 0) and 'ODA-SECURITY-' in record.get('stderr', '')
            record['passed'] = expected and snapshot(fixture) == before
            rows.append(record)
    return rows


def native_codex(output, executable, env):
    fixture = output / 'native-fixture'
    workspace = fixture / 'workspace'
    outside = fixture / 'outside'
    home = fixture / 'config'
    for path in [workspace, outside, home]:
        path.mkdir(parents=True)
    (workspace / 'read-marker').write_text('workspace-marker')
    (outside / 'read-marker').write_text('outside-marker')
    (workspace / 'escape').symlink_to(outside, target_is_directory=True)
    child_env = dict(env, CODEX_HOME=str(home))
    probe = fixture / 'probe.py'
    probe.write_text('''import json,pathlib,socket,sys
operation,path=sys.argv[1:3]
try:
 if operation=='read': pathlib.Path(path).read_bytes()
 elif operation=='write': pathlib.Path(path).write_text('harmless sandbox probe')
 elif operation=='connect':
  with socket.create_connection(('127.0.0.1',int(path)),timeout=2): pass
 print(json.dumps({'operation':operation,'outcome':'allowed'}))
except OSError as error:
 print(json.dumps({'operation':operation,'outcome':'denied','errno':error.errno}))
''')
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(5)
    port = listener.getsockname()[1]
    with socket.create_connection(('127.0.0.1', port), timeout=2):
        pass
    tests = [
        ('workspace-read', ':workspace', 'read', str(workspace / 'read-marker'), 'allowed'),
        ('outside-read-observation', ':workspace', 'read', str(outside / 'read-marker'), 'allowed'),
        ('workspace-write', ':workspace', 'write', str(workspace / 'write-marker'), 'allowed'),
        ('outside-write', ':workspace', 'write', str(outside / 'write-marker'), 'denied'),
        ('symlink-write', ':workspace', 'write', str(workspace / 'escape/symlink-marker'), 'denied'),
        ('traversal-write', ':workspace', 'write', str(workspace / '../outside/traversal-marker'), 'denied'),
        ('read-only-write', ':read-only', 'write', str(workspace / 'read-only-marker'), 'denied'),
        ('loopback-connect', ':workspace', 'connect', str(port), 'denied'),
    ]
    records = []
    try:
        for name, profile, operation, path, expected in tests:
            argv = [executable, 'sandbox', '--permission-profile', profile, '--cd', str(workspace), '--', sys.executable, str(probe), operation, path]
            record = run(argv, env=child_env)
            try:
                observation = json.loads(record['stdout'])
            except (ValueError, KeyError):
                observation = {'outcome': 'unknown'}
            record.update(case=name, kind='native-sandbox-observation', profile=profile,
                          observation=observation, expected=expected,
                          passed=record['returncode'] == 0 and observation['outcome'] == expected)
            records.append(record)
    finally:
        listener.close()
    # Independently inspect the actual markers. Exit status alone is not proof.
    escaped = [str(path) for path in outside.iterdir() if path.name != 'read-marker']
    readonly_written = (workspace / 'read-only-marker').exists()
    return dict(records=records, unexpected_outside_files=escaped,
                readonly_marker_exists=readonly_written,
                passed=all(row['passed'] for row in records) and not escaped and not readonly_written,
                config_snapshot=snapshot(home), fixture_snapshot=snapshot(fixture),
                scope='Direct Codex sandbox command only. No portable policy, model, approval UI, MCP, LSP, hooks, delegation, or credential isolation was tested.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agents', type=Path, required=True)
    parser.add_argument('--result-dir', type=Path, required=True)
    parser.add_argument('--native-probe', action='store_true')
    args = parser.parse_args()
    output = args.result_dir.resolve()
    if output.exists():
        parser.error('result directory must be new; preserve previous evidence')
    output.mkdir(parents=True)
    agents = args.agents.resolve()
    report = dict(schema_version='1.0.0', standard_version='1.1.0-draft.1',
                  timestamp=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                  implementation='reference-cli', agents_sha256=digest(agents),
                  runner_sha256=digest(__file__), python=sys.version,
                  python_executable=str(Path(sys.executable).resolve()),
                  source={name: run(['git', '-C', str(REPO / name), 'rev-parse', 'HEAD'])['stdout'].strip() for name in ['.', 'CLI', 'SPEC', 'WORKBENCH']},
                  dirty_source={name: bool(run(['git', '-C', str(REPO / name), 'status', '--porcelain'])['stdout']) for name in ['.', 'CLI', 'SPEC', 'WORKBENCH']},
                  host=prerequisites(), harnesses={},
                  supported=False, enforcement_pass=False,
                  adapter_cases=[dict(vendor=vendor, case=case, status='not-run', reason='Portable security activation is refused; no native policy projection exists') for vendor in PINS for case in SCENARIOS])
    report['refusals'] = refusal_evidence(agents, output)
    # Do not copy auth variables into native probes or read native user stores.
    native_env = {key: os.environ[key] for key in ['PATH', 'LANG', 'LC_ALL', 'SYSTEMROOT'] if key in os.environ}
    for vendor, pin in PINS.items():
        executable = shutil.which(vendor)
        row = dict(expected_version=pin, executable=executable, status='unavailable')
        if executable:
            version = run([executable, '--version'], env=native_env)
            row.update(version=version, executable_sha256=digest(executable), resolved_executable=str(Path(executable).resolve()), status='present')
            if not version_matches(version.get('stdout', ''), pin):
                row['status'] = 'version-mismatch'
        report['harnesses'][vendor] = row
    if args.native_probe and platform.system() == 'Linux' and report['harnesses']['codex']['status'] == 'present':
        report['codex_native'] = native_codex(output, report['harnesses']['codex']['executable'], native_env)
    missing = [name for name, path in report['host']['tools'].items() if not path]
    report['copilot_native'] = dict(status='not-run', missing_prerequisites=missing,
        reason='No credential-free direct sandbox probe interface or portable security adapter is implemented. Missing host prerequisites also block Linux sandbox verification.')
    report['claude_native'] = dict(status='skipped', reason='User has no account or subscription')
    report['refusal_pass'] = all(row['passed'] for row in report['refusals'])
    (output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(dict(result=str(output / 'result.json'), refusal_pass=report['refusal_pass'],
                         native_observation_pass=report.get('codex_native', {}).get('passed'),
                         supported=False, enforcement_pass=False)))
    return not (report['refusal_pass'] and report.get('codex_native', {}).get('passed', True))


if __name__ == '__main__':
    raise SystemExit(main())
