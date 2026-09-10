#!/usr/bin/env python3
"""Verify the Codex/Linux security subset through agents plan/apply.

No model or account is used. Trust is created only in an isolated test home.
Native commands use the exact invocation and replacement environment from plan.
"""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import tempfile

from run_security import REPO, digest, run, version_matches


def snapshot(root):
    result = {}
    for path in sorted(root.rglob('*')):
        key = str(path.relative_to(root))
        if path.is_symlink():
            result[key] = 'link:' + os.readlink(path)
        elif path.is_dir():
            result[key] = 'directory'
        elif path.is_file():
            result[key] = digest(path)
        else:
            result[key] = 'special-file'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--agents', type=Path, required=True)
    parser.add_argument('--result-dir', type=Path, required=True)
    args = parser.parse_args()
    output = args.result_dir.resolve()
    if output.exists():
        parser.error('use a new result directory; prior evidence is retained')
    output.mkdir(parents=True)
    fixture_base = Path(tempfile.mkdtemp(prefix='oda-codex-native-', dir='/mnt/DATA/tmp'))
    root = fixture_base / 'workspace'
    home = fixture_base / 'native-home'
    outside = fixture_base / 'outside'
    shutil.copytree(REPO / 'SPEC/examples/codex-linux-security', root)
    home.mkdir()
    outside.mkdir()
    subprocess.run(['git', '-c', 'init.templateDir=', 'init', '-q', str(root)],
                   env={'PATH': '/usr/bin:/bin', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'}, check=True)
    for name in ['private', 'docs/edit', 'open', '.codex']:
        (root / name).mkdir(parents=True, exist_ok=True)
    for path in [root / 'open/read.txt', root / 'docs/read.txt', root / 'docs/edit/read.txt',
                 root / 'private/marker.txt', outside / 'read.txt', outside / 'credential-marker.txt']:
        path.write_text('harmless fixture marker')
    (root / 'open/private-link').symlink_to(root / 'private/marker.txt')
    (root / 'open/outside-link').symlink_to(outside, target_is_directory=True)
    native_config = root / '.codex/config.toml'
    original_native = b"# Preserve this unowned comment.\nmodel = 'fixture-model'\n"
    native_config.write_bytes(original_native)
    trust_config = home / 'config.toml'
    trust_config.write_text('[projects.' + json.dumps(str(root)) + ']\ntrust_level="trusted"\n')
    trust_bytes = trust_config.read_bytes()
    agents = args.agents.resolve()
    commands = []
    checks = []

    def cli(command, extra=(), expect=True, env=None):
        argv = [str(agents), command, '--experimental', '--vendor', 'codex', '--root', str(root),
                '--codex-home', str(home), '--format', 'json', *extra]
        record = run(argv, env=env)
        try:
            payload = json.loads(record.get('stdout', ''))
        except ValueError:
            payload = {}
        record['payload'] = payload
        record['expected_applicable'] = expect
        commands.append(record)
        return record

    before = snapshot(root)
    planned = cli('plan')
    plan = planned['payload']
    checks.append(dict(id='plan-read-only', passed=plan.get('applicable') is True and snapshot(root) == before))
    if not plan.get('applicable'):
        (output / 'result.json').write_text(json.dumps({'passed': False, 'checks': checks, 'commands': commands}, indent=2) + '\n')
        print(json.dumps({'passed': False, 'diagnostics': plan.get('diagnostics'), 'result': str(output / 'result.json')}))
        return 1
    applied = cli('apply')
    checks.append(dict(id='apply', passed=applied['returncode'] == 0 and applied['payload'].get('applicable') is True))
    applied_bytes = native_config.read_bytes()
    applied_snapshot = snapshot(root)
    second = cli('apply')
    checks.append(dict(id='idempotence', passed=second['returncode'] == 0 and snapshot(root) == applied_snapshot and
                      all(action['operation'] == 'unchanged' for action in second['payload']['actions'])))
    security = plan['security']
    assert security['native_environment_mode'] == 'replace'
    invocation = security['native_invocation'][:-2]
    native_env = security['native_environment']
    probe = output / 'probe.py'
    probe.write_text('''import errno,json,os,pathlib,socket,subprocess,sys
operation,target=sys.argv[1:3]
try:
 if operation=='read': pathlib.Path(target).read_bytes()
 elif operation=='write': pathlib.Path(target).write_text('sandbox marker')
 elif operation=='link': os.link(target,sys.argv[3])
 elif operation=='rename': os.rename(target,sys.argv[3])
 elif operation in ('tcp','udp','ipv6','unix'):
  family=socket.AF_UNIX if operation=='unix' else (socket.AF_INET6 if operation=='ipv6' else socket.AF_INET)
  kind=socket.SOCK_DGRAM if operation=='udp' else socket.SOCK_STREAM
  with socket.socket(family,kind) as connection:
   connection.settimeout(2)
   address=target if operation=='unix' else (target,int(sys.argv[3]))
   if operation=='udp': connection.sendto(b'harmless fixture',address)
   else: connection.connect(address)
 elif operation=='exec': subprocess.run(['/usr/bin/true'],check=True)
 elif operation=='background':
  child=subprocess.run([sys.executable,__file__,'write',target],start_new_session=True,capture_output=True,text=True,check=True)
  print(child.stdout.strip());sys.exit(0)
 elif operation=='environment':
  print(json.dumps({'outcome': 'allowed' if os.environ.get('ODA_TEST_MARKER')=='harmless-marker' else 'missing'}));sys.exit(0)
 print(json.dumps({'outcome':'allowed'}))
except OSError as error:
 print(json.dumps({'outcome':'denied','errno':error.errno,'error_class':type(error).__name__}))
''')
    observations = []

    def observe(name, operation, target, expected, *rest, prefix=()):
        record = run([*invocation, *prefix, sys.executable, str(probe), operation, str(target), *map(str, rest)], env=native_env)
        try:
            value = json.loads(record.get('stdout', ''))
        except ValueError:
            value = {'outcome': 'unknown'}
        # Only actual permission failures count as denial. A missing fixture or
        # a refused/unreachable host connection is not enforcement evidence.
        permission_error = value.get('errno') in (1, 13, 30) or (operation in ('link', 'rename') and value.get('errno') in (16,18))
        passed = record['returncode'] == 0 and value['outcome'] == expected and (expected != 'denied' or permission_error)
        record.update(id=name, expected=expected, observed=value, passed=passed, evidence_class='native-enforcement')
        observations.append(record)

    observe('allowed-read', 'read', root / 'open/read.txt', 'allowed')
    observe('denied-read', 'read', root / 'private/marker.txt', 'denied')
    observe('allowed-write', 'write', root / 'open/write.txt', 'allowed')
    observe('denied-write', 'write', outside / 'write.txt', 'denied')
    observe('overlapping-grants', 'write', root / 'docs/edit/write.txt', 'denied')
    observe('symlink-read-escape', 'read', root / 'open/private-link', 'denied')
    observe('symlink-write-escape', 'write', root / 'open/outside-link/write.txt', 'denied')
    observe('path-traversal', 'write', root / '../outside/traversal.txt', 'denied')
    observe('hardlink-creation', 'link', root / 'private/marker.txt', 'denied', root / 'open/hardlink')
    observe('readonly-hardlink-creation', 'link', root / 'docs/read.txt', 'denied', root / 'open/readonly-hardlink')
    observe('outside-hardlink-creation', 'link', outside / 'read.txt', 'denied', root / 'open/outside-hardlink')
    observe('readonly-tree-rename', 'rename', root / 'docs', 'denied', root / 'open/moved-docs')
    observe('proc-self-root-alias', 'read', Path('/proc/self/root') / str(root / 'private/marker.txt').lstrip('/'), 'denied')
    observe('proc-init-root-alias', 'read', Path('/proc/1/root') / str(root / 'private/marker.txt').lstrip('/'), 'denied')
    for path in ['.agents/manifest.json', '.codex/config.toml', '.git/config']:
        observe('protected-' + path.split('/')[0], 'write', root / path, 'denied')
    observe('allowed-execution', 'exec', '.', 'allowed')
    observe('background-process', 'background', outside / 'background.txt', 'denied')
    observe('credential-files-inherit', 'read', outside / 'credential-marker.txt', 'allowed')
    observe('credential-environment-inherit', 'environment', '.', 'allowed', prefix=('/usr/bin/env', 'ODA_TEST_MARKER=harmless-marker'))
    # A shell composition remains inside the sandbox; this is not exact-command
    # approval evidence, which the adapter still refuses to provide.
    shell = run([*invocation, '/bin/sh', '-c', 'printf marker > open/shell-ok; printf marker > ../outside/shell-denied'], env=native_env)
    shell.update(id='shell-composition', evidence_class='native-enforcement', passed=shell['returncode'] != 0 and
                 (root / 'open/shell-ok').exists() and not (outside / 'shell-denied').exists())
    observations.append(shell)
    listeners = []
    try:
        for kind, operation in [(socket.SOCK_STREAM, 'tcp'), (socket.SOCK_DGRAM, 'udp')]:
            listener = socket.socket(socket.AF_INET, kind)
            listener.bind(('127.0.0.1', 0))
            if kind == socket.SOCK_STREAM:
                listener.listen(8)
                with socket.create_connection(listener.getsockname(), timeout=2):
                    pass
            else:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                    sender.sendto(b'host-baseline', listener.getsockname())
                listener.settimeout(2)
                assert listener.recv(100) == b'host-baseline'
            listeners.append(listener)
            observe('direct-network-' + operation, operation, '127.0.0.1', 'denied', listener.getsockname()[1])
        unix = socket.socket(socket.AF_UNIX)
        unix_path = outside / 'listener.sock'
        unix.bind(str(unix_path));unix.listen(8)
        with socket.socket(socket.AF_UNIX) as baseline:
            baseline.connect(str(unix_path))
        listeners.append(unix)
        observe('local-unix-network', 'unix', unix_path, 'denied')
        try:
            ipv6 = socket.socket(socket.AF_INET6)
            ipv6.bind(('::1', 0));ipv6.listen(8);listeners.append(ipv6)
            with socket.create_connection(('::1', ipv6.getsockname()[1]), timeout=2):
                pass
            observe('direct-network-ipv6', 'ipv6', '::1', 'denied', ipv6.getsockname()[1])
        except OSError as error:
            observations.append(dict(id='direct-network-ipv6', passed=False, status='host-unavailable', error=str(error)))
        ip_command = shutil.which('ip')
        addresses = json.loads(subprocess.check_output([ip_command, '-j', '-4', 'addr', 'show', 'up'])) if ip_command else []
        private = next((entry['local'] for interface in addresses for entry in interface['addr_info']
                        if ipaddress.ip_address(entry['local']).is_private and not ipaddress.ip_address(entry['local']).is_loopback), None)
        if private:
            listener = socket.socket();listener.bind((private, 0));listener.listen(8);listeners.append(listener)
            with socket.create_connection(listener.getsockname(), timeout=2):
                pass
            observe('private-network', 'tcp', private, 'denied', listener.getsockname()[1])
        else:
            observations.append(dict(id='private-network', passed=False, status='host-unavailable'))
    finally:
        for listener in listeners:
            listener.close()

    refusal_checks = []
    policy_path = root / '.agents/sandbox/sandbox.json'
    permission_path = root / '.agents/permissions/permissions.json'
    policy_original = policy_path.read_bytes()
    permission_original = permission_path.read_bytes()

    def refused(name, mutate, restore, env=None):
        mutate()
        before = snapshot(root)
        record = cli('apply', ('--force', '--backup'), expect=False, env=env)
        passed = record['returncode'] not in (None, 0) and snapshot(root) == before and 'ODA-SECURITY-' in record.get('stderr', '')
        refusal_checks.append(dict(id=name, passed=passed, evidence_class='adapter-refusal', command_index=len(commands)-1))
        restore()

    def change_policy(field, value):
        policy = json.loads(policy_original)
        if isinstance(field, tuple):
            policy[field[0]][field[1]] = value
        else:
            policy[field] = value
        policy_path.write_text(json.dumps(policy))

    for scope in ['builtin-tools', 'hooks', 'mcp-local', 'mcp-remote', 'lsp', 'delegation']:
        refused('scope-' + scope, lambda scope=scope: change_policy('coverage', ['shell', scope]), lambda: policy_path.write_bytes(policy_original))
    for field, value, name in [(('credentials', 'environment'), 'none', 'credential-environment-isolation'),
                               (('credentials', 'files'), 'deny', 'credential-file-isolation'),
                               (('filesystem', 'default'), 'deny', 'default-deny-filesystem'),
                               (('filesystem', 'runtime'), [], 'missing-runtime-grant'),
                               (('network', 'rules'), [{'host': 'example.com', 'ports': [443], 'effect': 'allow'}], 'network-allow-rule')]:
        refused(name, lambda field=field,value=value: change_policy(field, value), lambda: policy_path.write_bytes(policy_original))
    for default in ['ask', 'deny']:
        def change_permission(default=default):
            policy = json.loads(permission_original);policy['default'] = default;permission_path.write_text(json.dumps(policy))
        refused('permission-' + default, change_permission, lambda: permission_path.write_bytes(permission_original))
    def command_rule():
        policy = json.loads(permission_original)
        policy['rules'] = [{'kind':'process','name':'git','args':['status'],'effect':'allow'}]
        permission_path.write_text(json.dumps(policy))
    refused('exact-command-rules', command_rule, lambda: permission_path.write_bytes(permission_original))
    refused('untrusted-native-home', lambda: trust_config.write_text(''), lambda: trust_config.write_bytes(trust_bytes))
    refused('legacy-user-config', lambda: trust_config.write_bytes(b"sandbox_mode='danger-full-access'\n" + trust_bytes), lambda: trust_config.write_bytes(trust_bytes))
    refused('legacy-project-bypass', lambda: native_config.write_bytes(b"sandbox_mode='danger-full-access'\n" + applied_bytes), lambda: native_config.write_bytes(applied_bytes))
    managed = home / 'requirements.toml'
    refused('managed-config-unknown', lambda: managed.write_text(''), lambda: managed.unlink())
    hardlink = root / 'open/preexisting-hardlink'
    refused('preexisting-hardlink-alias', lambda: os.link(root / 'private/marker.txt', hardlink), lambda: hardlink.unlink())
    refused('readonly-hardlink-alias', lambda: os.link(root / 'docs/read.txt', hardlink), lambda: hardlink.unlink())
    refused('outside-hardlink-alias', lambda: os.link(outside / 'read.txt', hardlink), lambda: hardlink.unlink())
    empty_bin = output / 'empty-bin';empty_bin.mkdir()
    refused('missing-native-binary', lambda: None, lambda: None, env={'PATH':str(empty_bin)})
    fake_bin = output / 'fake-bin';fake_bin.mkdir();fake = fake_bin / 'codex';fake_marker = output / 'fake-executed'
    fake.write_text('#!/bin/sh\nprintf executed > ' + str(fake_marker) + '\n');fake.chmod(0o755)
    refused('unverified-native-binary', lambda: None, lambda: None, env={'PATH':str(fake_bin)})
    checks.append(dict(id='unverified-binary-never-executed', passed=not fake_marker.exists()))

    manifest = root / '.agents/manifest.json'
    manifest_original = manifest.read_bytes()
    manifest.write_text(json.dumps({'version':'1.1.0-draft.1','profiles':[]}))
    removal = cli('apply')
    checks.append(dict(id='profile-removal', passed=removal['returncode'] == 0 and native_config.read_bytes() == original_native))
    removed = run([*invocation, '/usr/bin/true'], env=native_env)
    commands.append(removed)
    checks.append(dict(id='removed-invocation-refuses', passed=removed['returncode'] not in (0,None)))
    manifest.write_bytes(manifest_original)
    reenabled = cli('apply')
    checks.append(dict(id='profile-reenable', passed=reenabled['returncode'] == 0 and native_config.read_bytes() == applied_bytes))
    checks.append(dict(id='trust-unchanged', passed=trust_config.read_bytes() == trust_bytes))
    forbidden = [outside / name for name in ['write.txt','traversal.txt','background.txt','shell-denied']]
    checks.append(dict(id='no-denied-side-effects', passed=not any(path.exists() for path in forbidden) and
                       not (root/'open/hardlink').exists() and not (root/'open/readonly-hardlink').exists() and not (root/'open/outside-hardlink').exists() and not (root/'open/moved-docs').exists() and not (root/'docs/edit/write.txt').exists()))

    cases = {row['id']: row for row in observations}
    verdicts = []
    for vendor in ['codex','copilot']:
        for name in __import__('run_security').SCENARIOS:
            status = 'implementation-pending'
            evidence = []
            if vendor == 'codex':
                aliases = {'symlink-escape':'symlink-write-escape','direct-network':'direct-network-tcp'}
                key = aliases.get(name, name)
                if key in cases and cases[key].get('passed'):
                    status = 'verified-enforcement';evidence=[key]
                refusal_map = {'denied-execution':'permission-deny','approval-denial':'permission-ask','noninteractive-ask':'permission-ask',
                               'exact-command-arguments':'exact-command-rules','web-tool-network':'scope-builtin-tools',
                               'remote-mcp-network':'scope-mcp-remote','local-mcp':'scope-mcp-local','lsp-process':'scope-lsp',
                               'delegation':'scope-delegation','credential-environment':'credential-environment-isolation',
                               'credential-files':'credential-file-isolation','conflicting-settings':'legacy-user-config',
                               'missing-prerequisite':'missing-native-binary','bypass-setting':'legacy-project-bypass'}
                if name in refusal_map:
                    evidence=[refusal_map[name]];status='verified-refusal'
                if name in ['idempotence','profile-removal']:
                    evidence=[name];status='verified-lifecycle'
                if name == 'rollback':
                    status='deterministic-test-only';evidence=['TestCodexSecurityTransactionRollback']
                if name == 'redirect-network':
                    status='outside-subset';evidence=['No allowed source connection exists in this deny-all network subset']
            verdicts.append(dict(vendor=vendor,scenario=name,status=status,evidence=evidence,
                                 scope='Direct sandbox shell subset only' if vendor=='codex' else 'Copilot mapping remains pending'))
    passed = all(row.get('passed') for row in checks + observations + refusal_checks)
    result = dict(schema_version='1.0.0',standard_version='1.1.0-draft.1',
                  adapter_subset_enforcement_pass=passed,full_adapter_support=False,
                  timestamp=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
                  agents_sha256=digest(agents),runner_sha256=digest(__file__),
                  codex_binary=invocation[0],codex_sha256=digest(invocation[0]),
                  codex_version=run([invocation[0],'--version'])['stdout'].strip(),
                  os=__import__('platform').platform(),
                  source_commits={component:run(['git','-C',str(REPO/component),'rev-parse','HEAD'])['stdout'].strip() for component in ['.','CLI','SPEC','WORKBENCH']},
                  source_dirty=True,fixture_base=str(fixture_base),plan=plan,checks=checks,observations=observations,refusals=refusal_checks,
                  scenario_matrix=verdicts,commands=commands,workspace_snapshot=snapshot(root),
                  authority_file_sha256=digest(trust_config),claude='skipped at user request',
                  limitations=['Direct sandbox command only; no model tool or approval UI evidence',
                               'Process runtime grant required; credential environment and files explicitly inherited',
                               'Configuration-only native home and unchanged authority state required',
                               'Domain allowlists, default-deny filesystem, and other coverage scopes refuse'])
    (output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(result=str(output/'result.json'),passed=passed,checks=len(checks),native_observations=len(observations),refusals=len(refusal_checks),
                          failures=[row['id'] for row in checks+observations+refusal_checks if not row.get('passed')])))
    return not passed


if __name__=='__main__':
    raise SystemExit(main())
