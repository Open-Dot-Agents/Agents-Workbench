#!/usr/bin/env python3
"""Run deterministic checks from source files without ignored local artifacts.

Copy tracked files and new non-ignored files from the working tree. Do not copy
Git metadata, ignored receipts, caches, or native homes. This checks the proposed
source changes before commit as well as clean CI checkouts.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def copy_source(destination):
    for component in ('', 'CLI', 'SPEC', 'WORKBENCH'):
        repository = ROOT / component
        names = subprocess.check_output(
            ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=repository)
        for raw in names.split(b'\0'):
            if not raw:
                continue
            name = os.fsdecode(raw)
            source = repository / name
            # Submodule entries are directories; copy each component separately.
            if not source.is_symlink() and (source.is_dir() or not source.exists()):
                continue
            target = destination / component / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_symlink():
                target.symlink_to(os.readlink(source))
            else:
                shutil.copy2(source, target)


def main():
    with tempfile.TemporaryDirectory(prefix='agents-clean-source-') as temporary:
        root = Path(temporary)
        copy_source(root)
        # Source copies have no Git metadata. Fixture tests can create partial
        # Git state; it must not be used to stamp these test-only binaries.
        env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1',
               'GOFLAGS': (os.environ.get('GOFLAGS', '') + ' -buildvcs=false').strip()}
        checks = [
            ('SPEC', [sys.executable, 'conformance/run.py']),
            ('SPEC', [sys.executable, 'conformance/security_draft.py']),
            ('SPEC', [sys.executable, 'conformance/native_draft.py']),
            ('CLI', ['go', 'test', './...']),
            ('CLI', ['go', 'run', './cmd/agents', 'validate', '--experimental', '--root', '..', '--format', 'json']),
            ('', [sys.executable, 'CLI/scripts/native_coverage_test.py']),
            ('', [sys.executable, 'CLI/scripts/check_compatibility.py']),
            ('WORKBENCH', [sys.executable, '-m', 'unittest', 'discover', '-s', 'task/test', '-p', '*_test.py']),
            ('WORKBENCH', [sys.executable, '-m', 'unittest', 'discover', '-s', 'conformance', '-p', '*_test.py']),
        ]
        failed = []
        for component, command in checks:
            print(f'Clean source ({component or "root"}): {" ".join(command)}', flush=True)
            result = subprocess.run(command, cwd=root / component, env=env)
            if result.returncode:
                failed.append((component, command))
        if failed:
            raise SystemExit(f'{len(failed)} clean-source checks failed')
        print('All clean-source checks passed; native support was not tested.')


if __name__ == '__main__':
    main()
