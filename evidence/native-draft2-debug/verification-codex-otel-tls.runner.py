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
    parser.add_argument('--with-preferences', action='store_true')
    parser.add_argument('--with-subagents', action='store_true')
    parser.add_argument('--with-codex-skills', action='store_true')
    parser.add_argument('--with-codex-otel', action='store_true')
    parser.add_argument('--with-codex-otel-tls', action='store_true')
    args = parser.parse_args()
    if args.with_codex_otel_tls and any((args.with_git, args.with_mcp, args.with_legacy, args.with_preferences, args.with_subagents, args.with_codex_skills, args.with_codex_otel)):
        parser.error('select one current review matrix')
    if args.with_codex_otel and any((args.with_git, args.with_mcp, args.with_legacy, args.with_preferences, args.with_subagents, args.with_codex_skills)):
        parser.error('select one current review matrix')
    if args.with_codex_skills and any((args.with_git, args.with_mcp, args.with_legacy, args.with_preferences, args.with_subagents)):
        parser.error('select one current review matrix')
    if args.with_subagents: args.with_preferences = True
    if args.with_preferences: args.with_legacy = True
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

    local_label = 'selection-subagents-reviewed' if args.with_subagents else 'selection-preferences-reviewed' if args.with_preferences else 'selection-legacy-reviewed' if args.with_legacy else 'selection-mcp-reviewed' if args.with_mcp else 'selection-git-reviewed' if args.with_git else 'selection-verified'
    native = [f'WORKBENCH/evidence/plugin-standard/{vendor}-{local_label}-{scope}.json'
              for vendor in ('codex', 'copilot') for scope in ('project', 'user')]
    if args.with_git:
        git_label = 'git-subagents-reviewed' if args.with_subagents else 'git-preferences-reviewed' if args.with_preferences else 'git-legacy-reviewed' if args.with_legacy else 'git-mcp-reviewed' if args.with_mcp else 'git-verified'
        native += [f'WORKBENCH/evidence/plugin-standard/{vendor}-{git_label}-{scope}.json'
                   for vendor in ('codex', 'copilot') for scope in ('project', 'user')]
    if args.with_mcp:
        for vendor in ('codex', 'copilot'):
            for scope in ('project', 'user'):
                for mode in ['approve-allow', 'prompt-allow', 'prompt-deny'] + (['never-allow'] if vendor == 'codex' else []):
                    native.append(f'WORKBENCH/evidence/plugin-standard/{vendor}-mcp-final-{mode}-{scope}.json')
    if args.with_legacy:
        label = 'subagents' if args.with_subagents else 'preferences' if args.with_preferences else 'final'
        native += [f'WORKBENCH/evidence/native-draft2-debug/copilot-legacy-{label}-{case}.json' for case in ('missing', 'conflict', 'nested')]
    if args.with_preferences:
        label = 'subagents-verified' if args.with_subagents else 'final'
        native.append(f'WORKBENCH/evidence/native-draft2-debug/copilot-preferences-{label}.json')
    if args.with_subagents:
        native += [f'WORKBENCH/evidence/native-draft2-debug/copilot-subagents-verified-{case}.json' for case in ('inherit', 'override', 'disabled', 'limits')]
    if args.with_codex_skills:
        native = [f'WORKBENCH/evidence/native-draft2-debug/codex-skills-verified-{scope}.json' for scope in ('user', 'project')]
    if args.with_codex_otel:
        native = ['WORKBENCH/evidence/native-draft2-debug/codex-otel-adapter-final.json']
    if args.with_codex_otel_tls:
        native = ['WORKBENCH/evidence/native-draft2-debug/codex-otel-tls-http-final.json']
        native += ['WORKBENCH/evidence/native-draft2-debug/codex-otel-tls-ca-' + paths + '-final.json' for paths in ('relative', 'absolute', 'home')]
        native += ['WORKBENCH/evidence/native-draft2-debug/codex-otel-tls-identity-' + signal + '-' + key + '-final.json'
                   for signal in ('exporter', 'trace_exporter', 'metrics_exporter') for key in ('ec', 'rsa')]

    def native_records():
        packages = []
        mcp_packages = []
        for name in native:
            path = ROOT / name
            record = json.loads(path.read_text())
            assert record['passed'], name
            assert sha(path.with_suffix('.runner.py')) == record['runner_sha256'], name
            runner = 'run_native_copilot_subagents.py' if Path(name).name.startswith('copilot-subagents-verified-') else 'run_native_copilot_preferences.py' if Path(name).name.startswith('copilot-preferences-') else 'run_native_copilot_legacy.py' if Path(name).name.startswith('copilot-legacy-') else 'run_native_plugin_mcp.py' if '-mcp-final-' in name else 'run_native_plugin_git.py' if Path(name).name.startswith(('codex-git-', 'copilot-git-')) else 'run_native_plugin_selections.py'
            if Path(name).name.startswith('codex-skills-verified-'): runner = 'run_native_codex_skills.py'
            if Path(name).name.startswith('codex-otel-adapter-'): runner = 'run_native_codex_otel.py'
            if Path(name).name.startswith('codex-otel-tls-http-'): runner = 'run_native_codex_otel.py'
            elif Path(name).name.startswith('codex-otel-tls-'): runner = 'run_native_codex_otel_tls.py'
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
            elif runner == 'run_native_copilot_preferences.py':
                assert record['native_version'] == '1.0.83', name
                assert record['native_sha256'] == 'a3262c4513ef1fc2ca21485261ca73196977ad76bd5e7990fb572f6134aaeedd', name
                assert [p['name'] for p in record['phases']] == ['plan', 'interactive', 'removed'], name
                for phase in record['phases']:
                    assert phase['preferences'] == phase['projected_preferences'] == phase['reimported_preferences'], name
                    assert phase['state_unchanged_by_apply'] and phase['settings_unchanged_by_native'], name
                    assert phase['settings_mode'] == 0o600 and phase['alive_before_teardown'], name
                assert len(record['phases'][0]['status_events']) >= 3, name
                assert len(record['phases'][1]['status_events']) >= 3, name
                assert not record['phases'][2]['status_events'], name
            elif runner == 'run_native_copilot_subagents.py':
                assert record['native_version'] == '1.0.83', name
                assert record['native_sha256'] == 'a3262c4513ef1fc2ca21485261ca73196977ad76bd5e7990fb572f6134aaeedd', name
                assert record['configuration'] == record['reimported_preferences'], name
                assert record['state_unchanged_by_apply'] and record['settings_mode'] == 0o600, name
                assert record['source_before_import'] == record['source_after_import'], name
                if record['case'] == 'disabled':
                    assert 'ODA Fixture' not in record['available_agents'] and not record['marker'], name
                else:
                    assert record['marker'] == 'ODA_DISPATCH_EFFECT' and any(a['approved'] for a in record['approvals']), name
                    if record['case'] == 'limits':
                        assert record['configured_limits_enforced'] is False, name
                        assert len(record['configured_agents']) == 2, name
                    else:
                        assert record['selected_model_observed'], name
                        model = 'gpt-5-mini' if record['case'] == 'override' else 'gpt-5.4'
                        effort = 'high' if record['case'] == 'override' else 'low'
                        assert record['wire_models'] == ['gpt-5.4', model, model, 'gpt-5.4'], name
                        assert record['wire_reasoning_efforts'] == ['low', effort, effort, 'low'], name
            elif runner == 'run_native_codex_otel_tls.py':
                assert record['native_version'] == '0.154.0', name
                assert record['native_sha256'] == '3188814c35471432d4123203e0eb38e5bddc60226e3d7ddf0e59e649ea140022', name
                assert record['source_tls_before'] == record['source_tls_after'], name
                assert 'CLI/internal/config/native_schemas/codex-0.154.0.json' in record['implementation_sha256'], name
                assert record['collector_control']['path'] == '/control', name
                if record['expect_identity_failure']:
                    assert record['tls_mode'] == 'mutual' and not record['adapter'] and len(record['phases']) == 1, name
                    assert record['collector_control']['client_serial'], name
                    phase = record['phases'][0]
                    assert phase['native_identity_failure'] and phase['native_exit_code'] == 0 and phase['config_unchanged'], name
                    assert not phase['exports'] and phase['requests'] and 'builder error' in phase['stderr'], name
                else:
                    assert record['tls_mode'] == 'ca' and record['adapter'], name
                    assert [p['label'] for p in record['phases']] == ['source', 'relocated'], name
                    for phase in record['phases']:
                        assert phase['correlated'] and phase['config_unchanged'] and phase['requests'], name
                        assert {e['path'] for e in phase['exports']} == {'/logs', '/traces'}, name
                    for signal in ('exporter', 'trace_exporter'):
                        value = record['imported_config']['otel'][signal]['otlp-http']['tls']['ca-certificate']
                        want = record['references']['ca-certificate']
                        if record['paths'] == 'relative': want = str(Path(record['fixture']) / 'source' / want)
                        assert value == want, name
            elif runner == 'run_native_codex_otel.py':
                assert record['native_version'] == '0.154.0' and record['adapter'], name
                assert record['native_sha256'] == '3188814c35471432d4123203e0eb38e5bddc60226e3d7ddf0e59e649ea140022', name
                assert [p['label'] for p in record['phases']] == ['redacted', 'prompt', 'project'], name
                for phase in record['phases']:
                    assert phase['correlated'] and phase['user_config_unchanged'], name
                    assert phase['exports'] and phase['requests'], name
                    logs = [e for e in phase['exports'] if e['path'].endswith('/logs')]
                    traces = [e for e in phase['exports'] if e['path'].endswith('/traces')]
                    assert logs and traces and phase['thread_id'] in json.dumps(logs), name
                    assert (phase['prompt'] in json.dumps(logs)) is (phase['label'] == 'prompt'), name
                    assert all(e['path'].startswith('/' + phase['label'] + '/') for e in phase['exports']), name
                assert any(c['exit_code'] != 0 and 'otel' in c['stderr'] for c in record['commands']), name
            elif runner == 'run_native_codex_skills.py':
                assert record['native_version'] == '0.154.0', name
                assert record['native_sha256'] == '3188814c35471432d4123203e0eb38e5bddc60226e3d7ddf0e59e649ea140022', name
                if record['scope'] == 'user':
                    assert record['source_before'] == record['source_after'], name
                    assert record['imported_preferences'] == record['reimported_preferences'], name
                    assert len(record['discoveries']) == 4, name
                    assert all(d['catalog_matches_setting'] and d['discovery_matches_setting'] for d in record['discoveries']), name
                else:
                    assert record['project_refusal']['exit_code'] != 0 and record['user_config_unchanged'], name
                    assert record['project_control_written_by_fixture'], name
                    assert [d['catalog_matches_setting'] for d in record['discoveries']] == [False, True, False], name
                    assert [d['discovery_matches_setting'] for d in record['discoveries']] == [False, True, False], name
                assert all(d['completed_turn']['status'] == 'completed' and d['model_requests'] for d in record['discoveries']), name
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
        return {'records': native, 'shared_package_hashes': packages[0] if packages else None, 'shared_mcp_package_hashes': mcp_packages[0] if mcp_packages else None}

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
    if args.with_codex_otel:
        def otel_sources():
            entries = json.loads((ROOT / 'WORKBENCH/evidence/native-draft2-debug/codex-otel-sources.json').read_text())
            for entry in entries:
                assert sha(ROOT / entry['path']) == entry['sha256'], entry['path']
            for name in ('codex-otel-direct-first', 'codex-otel-adapter-first'):
                path = ROOT / ('WORKBENCH/evidence/native-draft2-debug/' + name + '.json')
                record = json.loads(path.read_text())
                assert record['passed'] and sha(path.with_suffix('.runner.py')) == record['runner_sha256'], name
            return entries
        check('telemetry-source-provenance-and-earlier-probes', otel_sources)
    if args.with_codex_otel_tls:
        def tls_sources():
            entries = json.loads((ROOT / 'WORKBENCH/evidence/native-draft2-debug/codex-otel-tls-sources.json').read_text())
            for entry in entries:
                assert sha(ROOT / entry['path']) == entry['sha256'], entry['path']
                assert entry['commit'] == '6b9826e3aa83b1a5947db50f4332cb9c65f1b340'
            failed = []
            for path in sorted((ROOT / 'WORKBENCH/evidence/native-draft2-debug').glob('codex-otel-tls-*.json')):
                if path.name.endswith('-final.json'): continue
                record = json.loads(path.read_text())
                if not isinstance(record, dict) or 'runner_sha256' not in record: continue
                assert sha(path.with_suffix('.runner.py')) == record['runner_sha256'], path
                if not record['passed']: failed.append(str(path.relative_to(ROOT)))
            assert len(failed) >= 7, 'failed TLS attempts must remain available'
            return {'sources': entries, 'retained_failures': failed}
        check('tls-source-provenance-and-retained-failures', tls_sources)
    if args.with_codex_skills:
        def skills_failures():
            names = ['codex-skills-first-project.json', 'codex-skills-absolute-project.json', 'codex-skills-catalog-project.json']
            for name in names:
                path = ROOT / 'WORKBENCH/evidence/native-draft2-debug' / name
                record = json.loads(path.read_text())
                assert record['passed'] is False, name
                assert sha(path.with_suffix('.runner.py')) == record['runner_sha256'], name
            return names
        check('retained-native-project-selector-failures', skills_failures)
        def skills_sources():
            manifest = ROOT / 'WORKBENCH/evidence/native-draft2-debug/codex-skills-sources.json'
            entries = json.loads(manifest.read_text())['sources']
            for entry in entries: assert sha(ROOT / entry['path']) == entry['sha256'], entry['path']
            return entries
        check('skill-source-provenance', skills_sources)
    if args.with_preferences:
        def preference_failure():
            path = ROOT / 'WORKBENCH/evidence/native-draft2-debug/copilot-preferences-first.json'
            record = json.loads(path.read_text())
            assert record['passed'] is False and record['error'] == 'status output/padding not observed'
            assert sha(path.with_suffix('.runner.py')) == record['runner_sha256']
            return str(path.relative_to(ROOT))
        check('retained-preference-terminal-assertion', preference_failure)
    if args.with_subagents:
        def subagent_failure():
            path = ROOT / 'WORKBENCH/evidence/native-draft2-debug/copilot-subagents-first-override.json'
            record = json.loads(path.read_text())
            assert record['passed'] is False and record['error'] == "'function'"
            assert sha(path.with_suffix('.runner.py')) == record['runner_sha256']
            assert record['marker'] == 'ODA_DISPATCH_EFFECT'
            inherited = ROOT / 'WORKBENCH/evidence/native-draft2-debug/copilot-subagents-final-inherit.json'
            record = json.loads(inherited.read_text())
            assert record['passed'] is False and record['error'] == "'contextTier'"
            assert sha(inherited.with_suffix('.runner.py')) == record['runner_sha256']
            assert record['marker'] == 'ODA_DISPATCH_EFFECT'
            return [str(path.relative_to(ROOT)), str(inherited.relative_to(ROOT))]
        check('retained-subagent-tool-parser-assertion', subagent_failure)
        def preference_teardown_failure():
            path = ROOT / 'WORKBENCH/evidence/native-draft2-debug/copilot-preferences-subagents.json'
            record = json.loads(path.read_text())
            assert record['passed'] is False and record['error'] == 'Could not terminate the child.'
            assert sha(path.with_suffix('.runner.py')) == record['runner_sha256']
            return str(path.relative_to(ROOT))
        check('retained-preference-teardown-failure', preference_teardown_failure)
        def subagent_sources():
            manifest = ROOT / 'WORKBENCH/evidence/native-draft2-debug/copilot-subagents-sources.json'
            sources = json.loads(manifest.read_text())['sources']
            for entry in sources:
                assert sha(ROOT / entry['path']) == entry['sha256'], entry['path']
            return {entry['path']: entry['sha256'] for entry in sources}
        check('subagent-source-provenance', subagent_sources)

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
    if args.with_preferences:
        check('preference-runner-syntax', lambda: compile((ROOT / 'WORKBENCH/conformance/run_native_copilot_preferences.py').read_text(), 'preference-runner', 'exec') and 'valid')
    if args.with_subagents:
        check('subagent-runner-syntax', lambda: compile((ROOT / 'WORKBENCH/conformance/run_native_copilot_subagents.py').read_text(), 'subagent-runner', 'exec') and 'valid')
    if args.with_codex_skills:
        check('skill-runner-syntax', lambda: compile((ROOT / 'WORKBENCH/conformance/run_native_codex_skills.py').read_text(), 'skill-runner', 'exec') and 'valid')
    if args.with_codex_otel:
        check('telemetry-runner-syntax', lambda: compile((ROOT / 'WORKBENCH/conformance/run_native_codex_otel.py').read_text(), 'telemetry-runner', 'exec') and 'valid')
    if args.with_codex_otel_tls:
        check('tls-runner-syntax', lambda: compile((ROOT / 'WORKBENCH/conformance/run_native_codex_otel_tls.py').read_text(), 'tls-runner', 'exec') and 'valid')
    files = set(docs + ['.agents/features/coverage.json', 'scripts/native_coverage.py', 'scripts/native_coverage_semantics.py', 'scripts/native_coverage_test.py',
                        'WORKBENCH/conformance/run_native_plugin_selections.py', 'SPEC/conformance/native_draft.py'])
    if args.with_git:
        files.add('WORKBENCH/conformance/run_native_plugin_git.py')
    if args.with_mcp:
        files.add('WORKBENCH/conformance/run_native_plugin_mcp.py')
    if args.with_legacy:
        files.add('WORKBENCH/conformance/run_native_copilot_legacy.py')
    if args.with_preferences:
        files.add('WORKBENCH/conformance/run_native_copilot_preferences.py')
    if args.with_subagents:
        files.add('WORKBENCH/conformance/run_native_copilot_subagents.py')
        files.add('WORKBENCH/evidence/native-draft2-debug/copilot-subagents-sources.json')
    if args.with_codex_skills:
        files.add('WORKBENCH/conformance/run_native_codex_skills.py')
        files.add('WORKBENCH/evidence/native-draft2-debug/codex-skills-sources.json')
    if args.with_codex_otel:
        files.add('WORKBENCH/conformance/run_native_codex_otel.py')
        files.add('WORKBENCH/evidence/native-draft2-debug/codex-otel-sources.json')
    if args.with_codex_otel_tls:
        files.update({'WORKBENCH/conformance/run_native_codex_otel.py', 'WORKBENCH/conformance/run_native_codex_otel_tls.py', 'WORKBENCH/evidence/native-draft2-debug/codex-otel-tls-sources.json'})
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
