#!/usr/bin/env python3
"""Run development workflows with snapshots of the installed native CLIs."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile

from development_cases import cases_for, run_case
from development_fixture import DevelopmentFixture, parse_native_version, write_json
from run_native_approvals import native_binary, sha
from verify_development import HELPERS, evaluate_case, source_paths, verify

ROOT = Path(__file__).resolve().parents[2]
BUILD_FLAGS = ['-trimpath', '-buildvcs=false']


class Unavailable(RuntimeError):
    """A required local facility is missing; no behavior claim is possible."""

    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details or {}


def checked_native(vendor, home):
    try:
        installed = native_binary(vendor).resolve(strict=True)
        installed_digest = sha(installed)
        binary = Path(tempfile.mkdtemp(prefix=vendor + '-binary-', dir=home)) / vendor
        with installed.open('rb') as source, binary.open('xb') as target:
            shutil.copyfileobj(source, target)
        binary.chmod(0o500)
        digest = sha(binary)
        if digest != installed_digest:
            raise Unavailable(vendor + ': binary changed while making the private copy')
        command = [str(binary), '--version']
        if vendor == 'copilot':
            command.append('--no-auto-update')
        result = subprocess.run(command, env={'PATH': '/usr/bin:/bin', 'HOME': str(home)},
                                cwd=home, capture_output=True, text=True, timeout=20)
        identity = {'native_sha256': digest, 'candidate_path': str(installed), 'execution_path': str(binary),
                    'version_output': result.stdout,
                    'version_stderr': result.stderr, 'version_exit_code': result.returncode,
                    'native_version': parse_native_version(vendor, result.stdout)}
        if result.returncode or not identity['native_version'] or sha(binary) != digest:
            raise Unavailable(vendor + ': version command failed or the private binary changed', identity)
        return binary, identity
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        raise Unavailable(str(error)) from error


def fresh_cli(supplied, directory):
    reference = directory / 'agents-reference'
    env = {**os.environ, 'GOFLAGS': '', 'GOCACHE': '/tmp/agents-gocache', 'GOPATH': '/tmp/agents-gopath'}
    command = ['go', 'build', *BUILD_FLAGS, '-o', str(reference), './cmd/agents']
    result = subprocess.run(command, cwd=ROOT / 'CLI', env=env, capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise Unavailable('fresh CLI build failed: ' + result.stderr)
    if not supplied.is_file() or sha(supplied) != sha(reference):
        raise Unavailable('CLI differs from a fresh build. From CLI run: GOCACHE=/tmp/agents-gocache '
                          'GOPATH=/tmp/agents-gopath GOFLAGS= go build -trimpath -buildvcs=false -o /tmp/agents-workflow ./cmd/agents')
    return {'command': command, 'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr,
            'supplied_sha256': sha(supplied), 'reference_sha256': sha(reference), 'matches_supplied': True}


def capture_fixture(fixture, target):
    """Archive configuration and fixture inputs, never native account stores."""
    names = []
    for root in (fixture.workspace / '.agents', fixture.workspace / '.codex', fixture.workspace / '.github'):
        if root.exists():
            names.extend(path for path in root.rglob('*') if path.is_file() and not path.is_symlink())
    names.extend(path for path in fixture.workspace.iterdir() if path.is_file() and not path.is_symlink())
    names.extend(path for path in fixture.native.iterdir() if path.name in
                 ('config.toml', 'config.json', 'settings.json', 'permissions-config.json', 'AGENTS.md', 'copilot-instructions.md') and path.is_file())
    names.extend(path for path in (fixture.native / 'rules/existing.rules',) if path.is_file())
    names.extend(path for path in (fixture.base / 'rollback.json',) if path.is_file())
    for path in names:
        destination = target / path.relative_to(fixture.base)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)


def run(args):
    output = args.output.resolve()
    artifacts = output.with_suffix('.artifacts')
    runner = output.with_suffix('.runner.py')
    if any(path.exists() for path in (output, artifacts, runner)):
        raise FileExistsError('Use a new output path; retained receipts must not be overwritten')
    output.parent.mkdir(parents=True, exist_ok=True)
    artifacts.mkdir(mode=0o700)
    shutil.copy2(__file__, runner)
    selected = ['codex', 'copilot'] if args.vendor == 'all' else [args.vendor]
    record = {'schema_version': '1', 'created_at': datetime.now(timezone.utc).isoformat(),
              'requested_vendors': selected, 'vendors': {}, 'full_adapter_support': False,
              'native_selection': 'installed-snapshot',
              'runner_sha256': sha(runner), 'helper_sha256': {}, 'implementation_sha256': {},
              'source_artifact_sha256': {}, 'fixture_sha256': {}}
    sources = source_paths(ROOT)
    for relative in sources:
        source = ROOT / relative
        target = artifacts / 'source' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        digest = sha(target)
        record['implementation_sha256'][relative] = digest
        record['source_artifact_sha256'][str(target.relative_to(output.parent))] = digest
    for name in HELPERS:
        record['helper_sha256'][name] = sha(Path(__file__).parent / name)
    write_json(output, record)
    unavailable = []
    try:
        with tempfile.TemporaryDirectory(prefix='agents-development-') as temporary:
            directory = Path(temporary)
            prepared = {}
            for vendor in selected:
                version_home = directory / (vendor + '-version')
                version_home.mkdir()
                try:
                    binary, identity = checked_native(vendor, version_home)
                except Unavailable as error:
                    unavailable.append(str(error))
                    record['vendors'][vendor] = {**error.details, 'status': 'unavailable', 'reason': str(error), 'cases': []}
                    continue
                identity['cases'] = []
                record['vendors'][vendor] = identity
                prepared[vendor] = binary, identity
                print(json.dumps({'vendor': vendor, 'native_version': identity['native_version'],
                                  'native_sha256': identity['native_sha256'], 'status': 'snapshotted'}), flush=True)
            record['cli_build'] = fresh_cli(args.agents_cli.resolve(), directory)
            # Fail once if this session cannot create the local model server.
            try:
                with socket.socket() as connection:
                    connection.bind(('127.0.0.1', 0))
            except OSError as error:
                raise Unavailable('local model endpoint is unavailable: ' + str(error)) from error
            env = {**os.environ, 'GOCACHE': '/tmp/agents-gocache', 'GOPATH': '/tmp/agents-gopath'}
            clean = subprocess.run([sys.executable, str(ROOT / 'WORKBENCH/conformance/check_clean_source.py')],
                                   cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
            record['clean_source'] = {'exit_code': clean.returncode, 'stdout': clean.stdout, 'stderr': clean.stderr}
            print(json.dumps({'check': 'clean-source', 'status': 'passed' if clean.returncode == 0 else 'failed'}), flush=True)
            for vendor in selected:
                if vendor not in prepared:
                    continue
                binary, identity = prepared[vendor]
                for name in cases_for(vendor):
                    row = {'case': name, 'checks': [], 'commands': [], 'sessions': []}
                    fixture = None
                    try:
                        # Check again before each new native fixture.
                        if sha(binary) != identity['native_sha256'] or sha(args.agents_cli) != record['cli_build']['supplied_sha256']:
                            raise Unavailable('binary changed during the campaign')
                        fixture = DevelopmentFixture(directory / vendor / name, vendor, binary, args.agents_cli.resolve(), identity)
                        row['commands'], row['sessions'] = fixture.commands, fixture.sessions
                        run_case(name, fixture, row, ROOT)
                    except Unavailable as error:
                        unavailable.append(str(error))
                        row['unavailable'] = str(error)
                    except Exception as error:
                        row['error'] = type(error).__name__ + ': ' + str(error)
                    finally:
                        if fixture:
                            capture_fixture(fixture, artifacts / vendor / name)
                            fixture.close()
                    row.update(evaluate_case(row, vendor, identity))
                    identity['cases'].append(row)
                    write_json(output, record)
                    print(json.dumps({'vendor': vendor, 'case': name, 'status': row['status'], 'errors': row['errors']}), flush=True)
    except (Unavailable, OSError, subprocess.TimeoutExpired) as error:
        unavailable.append(str(error))
        for identity in record['vendors'].values():
            if not identity['cases'] and identity.get('status') != 'unavailable':
                identity.update(status='unavailable', reason=str(error))
    for path in artifacts.rglob('*'):
        if path.is_file() and artifacts / 'source' not in path.parents:
            record['fixture_sha256'][str(path.relative_to(output.parent))] = sha(path)
    record['unavailable'] = unavailable
    record['source_unchanged'] = set(sources) == set(source_paths(ROOT)) and all(
        (ROOT / name).is_file() and sha(ROOT / name) == digest for name, digest in record['implementation_sha256'].items())
    write_json(output, record)
    verification = verify(output)
    print(json.dumps({'receipt': str(output), **verification}), flush=True)
    if verification['workflow_status'] == 'unavailable':
        return 2
    return 0 if verification['current_workflow_eligible'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vendor', choices=('codex', 'copilot', 'all'), default='all')
    parser.add_argument('--agents-cli', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        return run(args)
    except (OSError, ValueError) as error:
        parser.exit(2, str(error) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())
