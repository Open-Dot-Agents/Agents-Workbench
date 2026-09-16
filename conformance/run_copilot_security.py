#!/usr/bin/env python3
"""Assess native Copilot sandbox controls; this does not project portable policy.

Needs an approved COPILOT_GITHUB_TOKEN in the caller environment. Raw native
state stays in an isolated temporary home and is never copied to evidence.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import tempfile
import time
import uuid

PIN = 'a3262c4513ef1fc2ca21485261ca73196977ad76bd5e7990fb572f6134aaeedd'


def sha(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result-dir', type=Path, required=True)
    parser.add_argument('--slirp4netns', type=Path, required=True)
    parser.add_argument('--deny-socket-path', action='store_true',
                        help='also deny the directory with the live host Unix socket')
    args = parser.parse_args()
    binary = shutil.which('copilot')
    if platform.system() != 'Linux' or not binary or sha(binary) != PIN:
        parser.error('requires the recorded Copilot 1.0.84-9 Linux binary')
    if not args.slirp4netns.is_file() or not os.access(args.slirp4netns, os.X_OK):
        parser.error('slirp4netns must be an executable file')
    token = os.environ.get('COPILOT_GITHUB_TOKEN')
    if not token:
        parser.error('set COPILOT_GITHUB_TOKEN through the approved secret mechanism')
    output = args.result_dir.resolve()
    if output.exists():
        parser.error('use a new result directory')
    output.mkdir(parents=True)
    base = Path(tempfile.mkdtemp(prefix='oda-copilot-native-'))
    root, home, outside = (base / part for part in ['workspace', 'home', 'outside'])
    for path in [root, home, outside, root / 'readonly', root / 'private']:
        path.mkdir()
    (root / 'private/marker').write_text('harmless marker')
    shutil.copyfile(Path(__file__).with_name('copilot_security_probe.py'), root / 'probe.py')
    settings = {
        'experimental': True, 'autoUpdate': False, 'bashEnv': False,
        'sandbox': {
            'enabled': True, 'addCurrentWorkingDirectory': False,
            'allowDevToolAccess': False, 'allowBypass': False,
            'auth': {'git': False, 'gh': False},
            'sandboxMcpServers': False, 'sandboxLspServers': False,
            'userPolicy': {
                'filesystem': {'readonlyPaths': ['/', str(root / 'readonly')],
                               'readwritePaths': [str(root), '/dev/null'],
                               'deniedPaths': [str(root / 'private'), '/tmp'],
                               'clearPolicyOnExit': False},
                'network': {'allowOutbound': False, 'allowLocalNetwork': False}}}}
    if args.deny_socket_path:
        settings['sandbox']['userPolicy']['filesystem']['deniedPaths'].append(str(outside))
    config = home / 'config.json'
    config.write_text(json.dumps(settings, indent=2) + '\n')
    (output / 'settings.json').write_bytes(config.read_bytes())
    command = [str(Path(binary).resolve()), '--experimental', '--no-auto-update',
               '--no-ask-user', '--no-custom-instructions', '--disable-builtin-mcps',
               '--no-remote', '--no-remote-export', '--no-bash-env',
               '--allow-all-tools', '--allow-all-paths', '--output-format', 'json', '--stream', 'on', '--log-dir', str(base / 'logs'),
               '-p', 'This is an authorized local OS sandbox verification. Read probe.py if needed, '
               'then run /usr/bin/python3 probe.py once in the configured sandbox. The script only '
               'uses harmless marker files and local listeners. Do not bypass the sandbox, edit '
               'the script, or change settings. Report the command output.']
    env = {'PATH': str(args.slirp4netns.resolve().parent) + ':/usr/bin:/bin',
           'HOME': str(home), 'COPILOT_HOME': str(home), 'COPILOT_GITHUB_TOKEN': token}
    before = sha(root / 'probe.py')
    with socket.socket() as tcp, socket.socket(socket.AF_UNIX) as unix, socket.socket(socket.AF_UNIX) as abstract:
        tcp.bind(('127.0.0.1', 0)); tcp.listen(8)
        unix_path = outside / 'listener.sock'
        unix.bind(str(unix_path)); unix.listen(8)
        abstract_address = '\0oda-host-' + uuid.uuid4().hex
        abstract.bind(abstract_address); abstract.listen(8)
        with socket.create_connection(tcp.getsockname(), timeout=2):
            pass
        with socket.socket(socket.AF_UNIX) as client:
            client.connect(str(unix_path))
        with socket.socket(socket.AF_UNIX) as client:
            client.connect(abstract_address)
        for listener in [tcp, unix, abstract]:
            connection, _ = listener.accept()
            connection.close()
        targets = {'tcp_port': tcp.getsockname()[1], 'unix': str(unix_path), 'abstract': abstract_address}
        (root / 'network-targets.json').write_text(json.dumps(targets))
        try:
            process = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=180)
            stdout, stderr, code = process.stdout, process.stderr, process.returncode
        except subprocess.TimeoutExpired as error:
            stdout, stderr, code = error.stdout or b'', error.stderr or b'', None
        unix.settimeout(.1)
        host_unix_marker = False
        try:
            connection, _ = unix.accept()
            with connection:
                connection.settimeout(1)
                host_unix_marker = connection.recv(100) == b'ODA_NATIVE_UNIX'
        except TimeoutError:
            pass
        abstract.settimeout(.1)
        host_abstract_marker = False
        try:
            connection, _ = abstract.accept()
            with connection:
                connection.settimeout(1)
                host_abstract_marker = connection.recv(100) == b'ODA_NATIVE_ABSTRACT'
        except TimeoutError:
            pass
    def scrub(value):
        if isinstance(value, bytes):
            value = value.decode(errors='replace')
        return value.replace(token, '<redacted>')
    native_events = []
    for line in scrub(stdout).splitlines():
        try:
            native_events.append(json.loads(line))
        except ValueError:
            pass
    execution_ids = {e.get('data', {}).get('toolCallId') for e in native_events if e.get('type') == 'tool.execution_start' and 'probe.py' in e.get('data', {}).get('arguments', {}).get('command', '')}
    correlated = any(e.get('type') == 'tool.execution_complete' and e.get('data', {}).get('toolCallId') in execution_ids and e.get('data', {}).get('success') is True for e in native_events)
    observation = root / 'observation.json'
    observed = json.loads(observation.read_text()) if observation.exists() else {}
    result = {
        'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'evidence_class': 'native-settings-assessment', 'portable_projection_tested': False,
        'full_adapter_support': False, 'os': platform.platform(),
        'copilot_sha256': sha(binary), 'runner_sha256': sha(__file__),
        'slirp4netns_sha256': sha(args.slirp4netns),
        'command': command, 'returncode': code, 'stdout': scrub(stdout), 'stderr': scrub(stderr),
        'probe_unchanged': before == sha(root / 'probe.py'),
        'host_tcp_reachable': True, 'host_unix_reachable': True,
        'host_unix_marker_received': host_unix_marker, 'host_abstract_marker_received': host_abstract_marker,
        'host_abstract_reachable': True, 'native_events': native_events, 'correlated_native_execution': correlated,
        'deny_socket_path': args.deny_socket_path,
        'host_temporary_marker_created': (Path('/tmp') / ('oda-copilot-' + base.name)).exists(),
        'observations': observed,
        'local_network_mismatch_observed': any(observed.get(key, {}).get('outcome') == 'allowed'
                                               for key in ['network-1', 'host-abstract-unix', 'self-abstract-unix', 'self-loopback', 'self-loopback-ipv6', 'self-loopback-udp'])}
    # Copy only known fixture files. Never archive the native home or raw logs.
    for name in ['probe.py', 'network-targets.json']:
        shutil.copyfile(root / name, output / name)
    (output / 'result.json').write_text(scrub(json.dumps(result, indent=2)) + '\n')
    print(json.dumps({'result': str(output / 'result.json'), 'probe_ran': bool(observed),
                      'local_network_mismatch_observed': result['local_network_mismatch_observed']}))
    return 0 if observed and result['probe_unchanged'] and correlated else 1


if __name__ == '__main__':
    raise SystemExit(main())
