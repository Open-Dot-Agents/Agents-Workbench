#!/usr/bin/env python3
"""Record deterministic checks and verify the retained native plugin evidence."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--with-git', action='store_true')
    parser.add_argument('--with-mcp', action='store_true')
    parser.add_argument('--with-legacy', action='store_true')
    args = parser.parse_args()
    if args.with_legacy and args.with_mcp: parser.error('select one current review matrix')
    if args.with_mcp: args.with_git = True
    if args.with_legacy: args.with_git = True
    output = args.output.resolve()
    snapshot = output.with_suffix('.runner.py')
    assert not output.exists() and not snapshot.exists(), 'refuse evidence replacement'
    checks = []
    commands = [
        ('stable-conformance', '.', ['python3', 'SPEC/conformance/run.py']),
        ('draft1-conformance', '.', ['python3', 'SPEC/conformance/security_draft.py']),
        ('draft2-conformance', '.', ['python3', 'SPEC/conformance/native_draft.py']),
        ('go-race', 'CLI', ['go', 'test', '-race', './...']),
        ('go-vet', 'CLI', ['go', 'vet', './...']),
        ('workbench', 'WORKBENCH', ['python3', '-m', 'unittest', 'discover', '-s', 'task/test', '-p', '*_test.py']),
        ('compatibility', '.', ['python3', 'CLI/scripts/check_compatibility.py']),
        ('coverage', '.', ['python3', 'scripts/native_coverage.py', '--check']),
        ('coverage-tests', '.', ['python3', 'scripts/native_coverage_test.py']),
        ('repository', 'CLI', ['go', 'run', './cmd/agents', 'validate', '--root', '..', '--format', 'json']),
    ]
    for example in ('native-draft', 'plugins-draft'):
        commands.append((example, 'CLI', ['go', 'run', './cmd/agents', 'validate', '--root',
                                         '../SPEC/examples/' + example, '--experimental', '--format', 'json']))
    for repo in ('.', 'CLI', 'SPEC', 'WORKBENCH'):
        commands.append(('whitespace-' + repo, repo, ['git', 'diff', '--check']))
    for name, cwd, command in commands:
        run = subprocess.run(command, cwd=ROOT / cwd, capture_output=True, text=True, timeout=180)
        checks.append({'check': name, 'cwd': cwd, 'command': command, 'exit_code': run.returncode,
                       'stdout': run.stdout, 'stderr': run.stderr})
        print(name, run.returncode, flush=True)

    def check(name, operation):
        try:
            observation = operation()
            checks.append({'check': name, 'exit_code': 0, 'observation': observation})
        except Exception as error:
            checks.append({'check': name, 'exit_code': 1, 'error': str(error)})
        print(name, checks[-1]['exit_code'], flush=True)

    def json_files(repo):
        run = subprocess.run(['git', 'ls-files', '-z', '--modified', '--others', '--exclude-standard'],
                             cwd=ROOT / repo, capture_output=True, check=True)
        files = sorted({name.decode() for name in run.stdout.split(b'\0') if name.endswith(b'.json')})
        for name in files:
            json.loads((ROOT / repo / name).read_text())
        return {'files': len(files)}

    for repo in ('.', 'CLI', 'SPEC', 'WORKBENCH'):
        check('json-' + repo, lambda repo=repo: json_files(repo))

    local_label = 'selection-legacy-reviewed' if args.with_legacy else 'selection-mcp-reviewed' if args.with_mcp else 'selection-git-reviewed' if args.with_git else 'selection-verified'
    native = [f'WORKBENCH/evidence/plugin-standard/{vendor}-{local_label}-{scope}.json'
              for vendor in ('codex', 'copilot') for scope in ('project', 'user')]
    if args.with_git:
        git_label = 'git-legacy-reviewed' if args.with_legacy else 'git-mcp-reviewed' if args.with_mcp else 'git-verified'
        native += [f'WORKBENCH/evidence/plugin-standard/{vendor}-{git_label}-{scope}.json'
                   for vendor in ('codex', 'copilot') for scope in ('project', 'user')]
    if args.with_mcp:
        for vendor in ('codex', 'copilot'):
            for scope in ('project', 'user'):
                for mode in ['approve-allow', 'prompt-allow', 'prompt-deny'] + (['never-allow'] if vendor == 'codex' else []):
                    native.append(f'WORKBENCH/evidence/plugin-standard/{vendor}-mcp-final-{mode}-{scope}.json')
    if args.with_legacy:
        native += [f'WORKBENCH/evidence/native-draft2-debug/copilot-legacy-final-{case}.json' for case in ('missing', 'conflict', 'nested')]

    def native_records():
        packages = []
        mcp_packages = []
        for name in native:
            path = ROOT / name
            record = json.loads(path.read_text())
            assert record['passed'], name
            assert sha(path.with_suffix('.runner.py')) == record['runner_sha256'], name
            runner = 'run_native_copilot_legacy.py' if '-legacy-final-' in name else 'run_native_plugin_mcp.py' if '-mcp-final-' in name else 'run_native_plugin_git.py' if Path(name).name.startswith(('codex-git-', 'copilot-git-')) else 'run_native_plugin_selections.py'
            assert sha(ROOT / 'WORKBENCH/conformance' / runner) == record['runner_sha256'], name
            for source, digest in record['implementation_sha256'].items():
                assert sha(ROOT / source) == digest, source
            for source, digest in record['helper_sha256'].items():
                assert sha(ROOT / 'WORKBENCH/conformance' / source) == digest, source
            if runner == 'run_native_plugin_git.py':
                expected = 'first_package_hashes' if record['vendor'] == 'codex' else 'second_package_hashes'
                assert record['installed_hashes'] == record[expected], name
                assert record['first_revision'] != record['second_revision'], name
                assert any(e['method'] == 'POST' and e['status'] == 200 and e['path'].endswith('/git-upload-pack') for e in record['http_events']), name
                assert 'disabled_discovery' in record, name
            elif runner == 'run_native_copilot_legacy.py':
                assert record['source_before'] == record['source_after'], name
                assert record['native_migrated_preferences'] == record['imported_preferences'] == record['projected_preferences'], name
                assert record['pending_migration_refusal']['exit_code'] != 0, name
                assert 'would replace setting memory' in record['pending_migration_refusal']['stderr'], name
            elif runner == 'run_native_plugin_mcp.py':
                assert record['installed_package_hashes'] == record['package_hashes'], name
                assert record['standard_environment_conformance'] is (record['vendor'] == 'codex'), name
                approvals = record['sessions'][0]['approvals']
                if record['native_approval'] == 'prompt':
                    assert len(approvals) == 1 and approvals[0]['approved'] is (record['decision'] == 'allow'), name
                else:
                    assert not approvals, name
                calls = [e for e in record['mcp_events'] if e.get('method') == 'tools/call']
                assert len(calls) == int(record['native_approval'] != 'never' and record['decision'] == 'allow'), name
                if calls and approvals: assert calls[0]['recorded_at'] >= approvals[0]['responded_at'], name
                assert [s['phase'] for s in record['sessions']] == ['enabled', 'disabled'], name
                mcp_packages.append(record['package_hashes'])
            else:
                assert record['installed_package_hashes'] == record['package_hashes'], name
                packages.append(record['package_hashes'])
        assert all(package == packages[0] for package in packages), 'cross-client package bytes differ'
        assert not mcp_packages or all(package == mcp_packages[0] for package in mcp_packages), 'cross-client MCP package bytes differ'
        return {'records': native, 'shared_package_hashes': packages[0], 'shared_mcp_package_hashes': mcp_packages[0] if mcp_packages else None}

    check('native-results-and-current-source-hashes', native_records)

    def failed_records():
        names = [f'{vendor}-selection-final-{scope}.json' for vendor in ('codex', 'copilot') for scope in ('project', 'user')]
        names.append('copilot-selection-first-project.json')
        if args.with_git:
            names += ['codex-git-first-project.json', 'copilot-git-first-project.json', 'codex-git-probe-project.json']
        if args.with_mcp:
            names += [f'{vendor}-mcp-{stage}-project.json' for vendor in ('codex', 'copilot') for stage in ('first', 'probe', 'approved')]
            names += ['codex-mcp-prompt-project.json', 'copilot-mcp-denial-project.json']
        for name in names:
            path = ROOT / 'WORKBENCH/evidence/plugin-standard' / name
            record = json.loads(path.read_text())
            assert record['passed'] is False, name
            assert sha(path.with_suffix('.runner.py')) == record['runner_sha256'], name
        return names

    check('retained-failed-native-attempts', failed_records)

    def inventory():
        path = ROOT / '.agents/features/codex-copilot.json'
        assert sha(path) == 'f41f70b081e3920822c125f90a1c27b5bea920061f74510ed20dc49c419ed310'
        assert len(json.loads(path.read_text())['entries']) == 1679
        return sha(path)

    check('frozen-inventory', inventory)
    if args.with_legacy:
        def legacy_sources():
            source = json.loads((ROOT / 'WORKBENCH/evidence/native-draft2-debug/copilot-legacy-sources.json').read_text())
            assert sha(ROOT / source['native_help']) == source['native_help_sha256']
            assert sha(ROOT / source['reference']) == source['reference_sha256']
            assert source['native_sha256'] == 'a3262c4513ef1fc2ca21485261ca73196977ad76bd5e7990fb572f6134aaeedd'
            return source
        check('legacy-source-provenance', legacy_sources)
    if args.with_mcp:
        def environment_source():
            path = ROOT / 'WORKBENCH/evidence/plugin-standard/agent-plugins-environment-requirements.source.json'
            source = json.loads(path.read_text())
            assert source['source_sha256'] == '97a658b7dca3ce1b4c2266b95da300fa51d9dc4ade59d73168e5f9104272da18'
            assert sha(ROOT / source['excerpt']) == source['excerpt_sha256']
            return source
        check('standard-environment-source', environment_source)
    docs = ['docs/PLUGIN_STANDARD.md', 'docs/NATIVE_CONFIGURATION.md', 'docs/NATIVE_DEBUG_RESEARCH.md',
            'docs/VENDOR_EVIDENCE.md', 'CLI/README.md']

    def links():
        for name in docs:
            path = ROOT / name
            for target in re.findall(r'\]\(([^)]+)\)', path.read_text()):
                target = target.split('#')[0]
                if not target or '://' in target:
                    continue
                resolved = (path.parent / target).resolve()
                assert resolved == output or resolved.exists(), (name, target)
        return docs

    check('local-document-links', links)
    check('runner-syntax', lambda: compile((ROOT / 'WORKBENCH/conformance/run_native_plugin_selections.py').read_text(), 'plugin-runner', 'exec') and 'valid')
    if args.with_git:
        check('git-runner-syntax', lambda: compile((ROOT / 'WORKBENCH/conformance/run_native_plugin_git.py').read_text(), 'git-plugin-runner', 'exec') and 'valid')
    if args.with_mcp:
        check('mcp-runner-syntax', lambda: compile((ROOT / 'WORKBENCH/conformance/run_native_plugin_mcp.py').read_text(), 'mcp-plugin-runner', 'exec') and 'valid')
    if args.with_legacy:
        check('legacy-runner-syntax', lambda: compile((ROOT / 'WORKBENCH/conformance/run_native_copilot_legacy.py').read_text(), 'legacy-runner', 'exec') and 'valid')
    files = set(docs + ['.agents/features/coverage.json', 'scripts/native_coverage.py', 'scripts/native_coverage_test.py',
                        'WORKBENCH/conformance/run_native_plugin_selections.py', 'SPEC/conformance/native_draft.py'])
    if args.with_git:
        files.add('WORKBENCH/conformance/run_native_plugin_git.py')
    if args.with_mcp:
        files.add('WORKBENCH/conformance/run_native_plugin_mcp.py')
    if args.with_legacy:
        files.add('WORKBENCH/conformance/run_native_copilot_legacy.py')
    files.update(str(p.relative_to(ROOT)) for p in (ROOT / 'CLI/internal/config').glob('*.go'))
    files.update(str(p.relative_to(ROOT)) for p in (ROOT / 'SPEC/examples/plugins-draft').rglob('*') if p.is_file())
    result = {'checked_at': datetime.now(timezone.utc).isoformat(), 'revision': 'uncommitted working tree',
              'passed': all(c['exit_code'] == 0 for c in checks), 'full_adapter_support': False,
              'milestone_complete': False, 'native_evidence': native, 'checks': checks,
              'implementation_sha256': {name: sha(ROOT / name) for name in sorted(files)},
              'verification_runner_sha256': sha(__file__)}
    snapshot.write_bytes(Path(__file__).read_bytes())
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'passed': result['passed'], 'checks': len(checks), 'output': str(output)}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
