"""Deterministic workflow and verifier regressions; never native evidence."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout

from development_cases import COMMON_CASES, create_later_protected_file, run_case
from development_fixture import DevelopmentFixture, USER_FILES
from probe_development import Unavailable, checked_native, fresh_cli, run
from verify_development import HELPERS, doctor_errors, doctor_expectations, evaluate_case, session_errors, source_paths, tool_result, verify
from run_native_approvals import sha

ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC_IDENTITY = {'native_version': '0.154.0', 'native_sha256': '1' * 64,
                      'version_output': 'codex-cli 0.154.0\n', 'version_exit_code': 0}


class NoServer:
    server_port = 12345

    def __init__(self, *_):
        pass

    def serve_forever(self):
        pass

    def shutdown(self):
        pass

    def server_close(self):
        pass


class LifecycleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='agents-development-tests-')
        cls.base = Path(cls.temporary.name)
        cls.cli = cls.base / 'agents'
        cls.native = cls.base / 'native-must-not-run'
        cls.native.write_text('#!/bin/sh\necho launched > "$HOME/accidental-launch"\nexit 99\n')
        cls.native.chmod(0o755)
        subprocess.run(['go', 'build', '-buildvcs=false', '-o', str(cls.cli), './cmd/agents'],
                       cwd=ROOT / 'CLI', check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_projection_lifecycle(self):
        for vendor in ('codex', 'copilot'):
            for name in COMMON_CASES + (('legacy-migration',) if vendor == 'codex' else ()):
                with self.subTest(vendor=vendor, case=name), patch('development_fixture.ThreadingHTTPServer', NoServer):
                    fixture = DevelopmentFixture(self.base / vendor / name, vendor, self.native, self.cli)
                    row = {}
                    try:
                        with patch.object(fixture, 'session', return_value={}):
                            run_case(name, fixture, row, ROOT)
                        self.assertTrue(all(item['actual'] == item['expected'] for item in row.get('checks', [])))
                        self.assertEqual(doctor_errors(fixture.commands, name, vendor), [])
                        self.assertFalse((fixture.home / 'accidental-launch').exists())
                        self.assertEqual(fixture.sessions, [])  # These are not native tests.
                    finally:
                        fixture.close()

    def test_later_protected_file_replaces_readonly_native_placeholder(self):
        with patch('development_fixture.ThreadingHTTPServer', NoServer):
            fixture = DevelopmentFixture(self.base / 'later-placeholder', 'codex', self.native, self.cli)
            row, phases = {}, []

            def native_phase(label, **options):
                phases.append(label)
                path = fixture.workspace / 'future.env'
                if label == 'protected-later-before':
                    path.write_bytes(b'')
                    path.chmod(0o444)  # Captured Codex 0.154.0 behavior.
                else:
                    self.assertEqual(options['command'], 'cat future.env')
                    self.assertEqual(path.read_text(), 'AGENTS_PRIVATE_SENTINEL\n')
                return {}

            try:
                with patch.object(fixture, 'session', side_effect=native_phase):
                    run_case('protected-later', fixture, row, ROOT)
                self.assertEqual(phases, ['protected-later-before', 'protected-later'])
                self.assertEqual(row['protected_later_creation']['method'], 'replace-empty-native-placeholder')
                self.assertEqual(row['protected_later_creation']['before']['mode'], '0o444')
                self.assertEqual(row['protected_later_creation']['before']['size'], 0)
                policy = json.loads((fixture.workspace / '.agents/permissions/development.json').read_text())
                self.assertIn('future.env', policy['protected_paths'])
            finally:
                fixture.close()

    def test_global_scope_allows_native_settings_migration(self):
        with patch('development_fixture.ThreadingHTTPServer', NoServer):
            fixture = DevelopmentFixture(self.base / 'copilot-native-migration', 'copilot', self.native, self.cli)
            row, phases = {}, []

            def native_phase(label, **_):
                phases.append(label)
                # Copilot 1.0.84-9 moved this key during the captured session.
                settings = fixture.native / 'settings.json'
                state = fixture.native / 'config.json'
                value = json.loads(settings.read_text())
                if 'trustedFolders' in value:
                    state.write_text(json.dumps({'trustedFolders': value.pop('trustedFolders')}))
                    settings.write_text(json.dumps(value))
                return {}

            try:
                with patch.object(fixture, 'session', side_effect=native_phase):
                    run_case('global-scope', fixture, row, ROOT)
                self.assertEqual(phases, ['global-scope', 'copilot-global-override', 'copilot-global-fallback'])
                self.assertTrue(all(item['actual'] == item['expected'] for item in row['checks']))
                self.assertNotIn('trustedFolders', json.loads((fixture.native / 'settings.json').read_text()))
                self.assertIn('trustedFolders', json.loads((fixture.native / 'config.json').read_text()))
            finally:
                fixture.close()

    def test_global_scope_rejects_adapter_user_file_changes(self):
        real_run = subprocess.run
        for filename in ('settings.json', 'permissions-config.json', 'copilot-instructions.md'):
            with self.subTest(filename=filename), patch('development_fixture.ThreadingHTTPServer', NoServer):
                fixture = DevelopmentFixture(self.base / ('adapter-change-' + filename), 'copilot', self.native, self.cli)

                def corrupted_apply(command, **options):
                    completed = real_run(command, **options)
                    if command[1] == 'apply' and '--global' not in command:
                        core = fixture.workspace / '.agents/AGENTS.md'
                        if 'AGENTS_PROJECT_CORE' in core.read_text():
                            (fixture.native / filename).write_text('unexpected adapter write\n')
                    return completed

                try:
                    with patch.object(fixture, 'session', return_value={}), \
                         patch('development_fixture.subprocess.run', side_effect=corrupted_apply):
                        with self.assertRaises(AssertionError):
                            run_case('global-scope', fixture, {}, ROOT)
                    self.assertEqual((fixture.native / filename).read_text(), 'unexpected adapter write\n')
                finally:
                    fixture.close()


def synthetic_session(label='edit'):
    from development_cases import expectations
    present, absent = expectations(label)
    identifier, nonce = 'synthetic-session', 'SYNTHETIC_NONCE'
    return {'label': label, 'nonce': nonce, 'session_id': identifier, 'process_exited': True,
            'native_sha256': SYNTHETIC_IDENTITY['native_sha256'], 'native_version': SYNTHETIC_IDENTITY['native_version'],
            'expected_present': list(present), 'expected_absent': list(absent), 'approvals': [],
            'requests': [{'instructions': ' '.join((*present, nonce))}],
            'thread': {'thread': {'id': identifier}, 'approvalPolicy': 'on-request',
                       'activePermissionProfile': {'id': 'agents-development'}},
            'completion': {'threadId': identifier, 'turn': {'status': 'completed'}},
            'effective_config': {'config': {'model': 'fixture-global'}},
            'events': [{'session': identifier, 'text': nonce + '_DONE'}]}


def synthetic_operation(name='edit'):
    session = synthetic_session(name)
    session.update(command='fixture command', cwd='/disposable')
    session['requests'].append({'input': [
        {'type': 'function_call', 'call_id': 'development-call', 'name': 'exec_command',
         'arguments': json.dumps({'cmd': session['command'], 'workdir': session['cwd'], 'login': False})},
        {'type': 'function_call_output', 'call_id': 'development-call', 'output': 'Process exited with code 0\n'}]})
    return {'case': name, 'commands': synthetic_doctors(name),
            'sessions': [session], 'file_after': 'after\n', 'guidance_only': name.startswith('push-')}


def synthetic_doctors(name):
    """Verifier inputs only. These records never establish native evidence."""
    rows = []
    for phase, state in doctor_expectations(name, 'codex'):
        ready = state in ('current', 'not-selected')
        report = {'schema_version': '1.0.0', 'vendor': 'codex', 'scope': 'development-only',
                  'configuration_state': state, 'ready': ready, 'checks': [
                      {'id': 'session.authority', 'layer': 'active-session', 'status': 'unknown'},
                      {'id': 'executable.location', 'status': 'ok'}]}
        if not ready:
            report['checks'].append({'id': 'configuration.' + state, 'status': 'action-required'})
        snapshot = {'.': 'synthetic', 'home/settings.json': 'synthetic', 'workspace/.git/HEAD': 'synthetic'}
        rows.append({'command': ['agents', 'doctor', '--experimental', '--vendor', 'codex', '--format', 'json'],
                     'doctor_phase': phase, 'stdout': json.dumps(report), 'exit_code': 0 if ready else 1,
                     'expect_success': ready, 'authority_before': 'hash', 'authority_after': 'hash',
                     'inspection_before': dict(snapshot), 'inspection_after': dict(snapshot)})
    return rows


class VerifierTest(unittest.TestCase):
    def test_doctor_requires_phase_results_and_unchanged_files(self):
        for corrupt in ('missing-phase', 'state', 'authority', 'write', 'missing-snapshot', 'exit'):
            with self.subTest(corrupt=corrupt):
                rows = synthetic_doctors('edit')
                self.assertEqual(doctor_errors(rows, 'edit', 'codex'), [])
                if corrupt == 'missing-phase':
                    rows.pop()
                elif corrupt in ('state', 'authority'):
                    report = json.loads(rows[-1]['stdout'])
                    if corrupt == 'state':
                        report['configuration_state'] = 'needs-apply'
                    else:
                        report['checks'][0]['status'] = 'ok'
                    rows[-1]['stdout'] = json.dumps(report)
                elif corrupt == 'write':
                    rows[-1]['inspection_after']['home/settings.json'] = 'changed'
                elif corrupt == 'missing-snapshot':
                    rows[-1].pop('inspection_before')
                else:
                    rows[-1]['exit_code'] = 1
                self.assertTrue(doctor_errors(rows, 'edit', 'codex'))

    def test_user_scope_requires_per_command_file_evidence(self):
        user = {name: None for name in USER_FILES}
        command = {'command': ['agents', 'apply'], 'user_files_before': user, 'user_files_after': dict(user)}
        row = {'case': 'global-scope', 'commands': [command]}
        error = 'project commands did not preserve user files'
        self.assertNotIn(error, evaluate_case(row, 'copilot', SYNTHETIC_IDENTITY)['errors'])
        command['user_files_after']['copilot-instructions.md'] = 'changed'
        self.assertIn(error, evaluate_case(row, 'copilot', SYNTHETIC_IDENTITY)['errors'])
        command.pop('user_files_before')
        command.pop('user_files_after')
        self.assertIn(error, evaluate_case(row, 'copilot', SYNTHETIC_IDENTITY)['errors'])

    def test_missing_socket_after_binary_selection_is_unavailable(self):
        state = SimpleNamespace(integrity_valid=True, current_eligible=False, integrity_errors=(), eligibility_errors=())
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / 'results.json'
            args = SimpleNamespace(output=receipt, vendor='codex', agents_cli=Path(directory) / 'agents')
            build = {'matches_supplied': True, 'supplied_sha256': '2' * 64, 'reference_sha256': '2' * 64}
            with patch('probe_development.source_paths', return_value=[]), \
                 patch('verify_development.source_paths', return_value=[]), \
                 patch('probe_development.checked_native', return_value=(Path('/not-executed'), dict(SYNTHETIC_IDENTITY))), \
                 patch('probe_development.fresh_cli', return_value=build), \
                 patch('probe_development.socket.socket', side_effect=PermissionError('socket denied')), \
                 patch('verify_development.assess_receipt', return_value=state), redirect_stdout(io.StringIO()):
                self.assertEqual(run(args), 2)
                outcome = verify(receipt)
            self.assertEqual(outcome['workflow_status'], 'unavailable')
            self.assertEqual(outcome['workflow_errors'], [])
            self.assertIn('socket denied', outcome['unavailable'][0])

    def test_native_execution_uses_a_private_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installed = root / 'installed-copilot'
            installed.write_bytes(b'pinned native executable')
            home = root / 'isolated-version-home'
            home.mkdir()
            observed = []

            def version(command, **_):
                # An installer replaces the public path before process start.
                installed.write_bytes(b'updated native executable')
                observed.append(Path(command[0]).read_bytes())
                self.assertIn('--no-auto-update', command)
                return SimpleNamespace(returncode=0, stdout='GitHub Copilot CLI 1.0.83.\n', stderr='')

            with patch('probe_development.native_binary', return_value=installed), \
                 patch('probe_development.subprocess.run', side_effect=version):
                binary, identity = checked_native('copilot', home)
                self.assertNotEqual(binary, installed)
                self.assertEqual(observed, [b'pinned native executable'])
                self.assertEqual(sha(binary), identity['native_sha256'])

    def test_later_file_preparation_refuses_unexpected_content_and_aliases(self):
        for kind in ('contents', 'symlink', 'hardlink', 'directory'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path, other = root / 'future.env', root / 'other'
                other.write_bytes(b'preserve')
                if kind == 'contents':
                    path.write_bytes(b'preserve')
                elif kind == 'symlink':
                    path.symlink_to(other)
                elif kind == 'hardlink':
                    other.write_bytes(b'')
                    os.link(other, path)
                else:
                    path.mkdir()
                before = path.lstat()
                with self.assertRaisesRegex(AssertionError, 'will not replace'):
                    create_later_protected_file(root, {'missing_path_before': {'future.env': None}})
                self.assertEqual(path.lstat(), before)

    def test_later_file_creation_without_a_placeholder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = {'missing_path_before': {'future.env': None}}
            create_later_protected_file(root, row)
            self.assertEqual((root / 'future.env').read_text(), 'AGENTS_PRIVATE_SENTINEL\n')
            self.assertEqual(row['protected_later_creation']['method'], 'create-new-file')

    def test_later_boundary_requires_recorded_file_creation(self):
        row = synthetic_operation('protected-later')
        first = json.loads(json.dumps(synthetic_session('protected-later-before')).replace('synthetic-session', 'first-session'))
        row['sessions'].insert(0, first)
        row['sessions'][-1]['requests'][-1]['input'][-1]['output'] = 'Process exited with code 1\nPermission denied\n'
        row.update(missing_path_before={'future.env': None},
                   missing_path_after={'future.env': hashlib.sha256(b'').hexdigest()},
                   protected_later_creation={'method': 'replace-empty-native-placeholder',
                       'before': {'regular_file': True, 'mode': '0o444', 'size': 0, 'owned_by_fixture_user': True, 'links': 1},
                       'after': {'future.env': hashlib.sha256(b'AGENTS_PRIVATE_SENTINEL\n').hexdigest()}})
        self.assertEqual(evaluate_case(row, 'codex', SYNTHETIC_IDENTITY)['status'], 'passed')
        del row['protected_later_creation']
        self.assertEqual(evaluate_case(row, 'codex', SYNTHETIC_IDENTITY)['status'], 'failed')

    def test_unavailable_vendor_does_not_hide_another_vendor_failure(self):
        # This isolates report classification. It cannot qualify native evidence.
        state = SimpleNamespace(integrity_valid=True, current_eligible=False, integrity_errors=(), eligibility_errors=())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = root / 'unavailable.json'
            record = {'schema_version': '1', 'full_adapter_support': False, 'source_unchanged': True,
                      'helper_sha256': {name: '0' * 64 for name in HELPERS},
                      'requested_vendors': ['codex', 'copilot'], 'unavailable': ['missing binary'],
                      'vendors': {vendor: {'status': 'unavailable', 'reason': 'missing binary', 'cases': []}
                                  for vendor in ('codex', 'copilot')}}
            with patch('verify_development.assess_receipt', return_value=state):
                receipt.write_text(json.dumps(record))
                unavailable = verify(receipt, root)
                self.assertEqual(unavailable['workflow_status'], 'unavailable')
                self.assertEqual(unavailable['workflow_errors'], [])
                self.assertFalse(unavailable['current_workflow_eligible'])
                record['vendors']['codex'] = {**SYNTHETIC_IDENTITY,
                                             'cases': [{'case': 'protected-later', 'error': 'fixture failed'}]}
                receipt.write_text(json.dumps(record))
                failed = verify(receipt, root)
                self.assertEqual(failed['workflow_status'], 'failed')
                self.assertTrue(any('fixture failed' in message for message in failed['workflow_errors']))
                self.assertFalse(any(message.startswith('copilot:') for message in failed['workflow_errors']))
                self.assertFalse(failed['current_workflow_eligible'])

    def test_copilot_version_accepts_its_terminal_period(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'copilot'
            binary.write_text('synthetic binary')
            output = "GitHub Copilot CLI 1.0.83.\nRun 'copilot update' to check for updates.\n"
            completed = SimpleNamespace(returncode=0, stdout=output, stderr='')
            with patch('probe_development.native_binary', return_value=binary), \
                 patch('probe_development.subprocess.run', return_value=completed):
                _, identity = checked_native('copilot', Path(directory))
                self.assertEqual(identity['native_version'], '1.0.83')
                completed.stdout = 'GitHub Copilot CLI 1.0.84-9.\n'
                _, identity = checked_native('copilot', Path(directory))
                self.assertEqual(identity['native_version'], '1.0.84-9')
                for wrong in ('1.0.83.1', '1.0', '1.0.84-.', 'unknown'):
                    completed.stdout = 'GitHub Copilot CLI ' + wrong + '\n'
                    with self.assertRaises(Unavailable):
                        checked_native('copilot', Path(directory))

    def test_missing_operation_cannot_pass(self):
        row = synthetic_operation()
        self.assertEqual(evaluate_case(row, 'codex', SYNTHETIC_IDENTITY)['status'], 'passed')
        del row['sessions'][0]['command']
        self.assertEqual(evaluate_case(row, 'codex', SYNTHETIC_IDENTITY)['status'], 'failed')

    def test_tool_output_must_match_call_and_command(self):
        session = synthetic_operation()['sessions'][0]
        self.assertEqual(tool_result(session), (0, 'Process exited with code 0\n', True))
        for mutate in ('call', 'command', 'duplicate'):
            changed = copy.deepcopy(session)
            items = changed['requests'][-1]['input']
            if mutate == 'call':
                items[1]['call_id'] = 'another-call'
            elif mutate == 'command':
                changed['command'] = 'different command'
            else:
                items.append(copy.deepcopy(items[1]))
            self.assertFalse(tool_result(changed)[2])

    def test_native_completion_and_guidance_are_required(self):
        session = synthetic_session()
        self.assertEqual(session_errors(session, 'codex', SYNTHETIC_IDENTITY), [])
        for key in ('requests', 'events', 'completion', 'thread', 'effective_config', 'process_exited'):
            changed = copy.deepcopy(session)
            changed.pop(key)
            self.assertTrue(session_errors(changed, 'codex', SYNTHETIC_IDENTITY), key)

    def test_guidance_observations_never_count_as_enforcement(self):
        row = synthetic_operation('push-script')
        row.update(published=True, discarded=False, head_before='head', head_after='head')
        self.assertEqual(evaluate_case(row, 'codex', SYNTHETIC_IDENTITY)['status'], 'observed')
        row['guidance_only'] = False
        self.assertEqual(evaluate_case(row, 'codex', SYNTHETIC_IDENTITY)['status'], 'failed')

    def test_boundary_claim_requires_denial_and_no_leak(self):
        row = synthetic_operation('protected-read')
        self.assertEqual(evaluate_case(row, 'codex', SYNTHETIC_IDENTITY)['status'], 'failed')
        output = row['sessions'][0]['requests'][-1]['input'][-1]
        output['output'] = 'Process exited with code 1\nPermission denied\n'
        self.assertEqual(evaluate_case(row, 'codex', SYNTHETIC_IDENTITY)['status'], 'passed')
        output['output'] += 'AGENTS_PRIVATE_SENTINEL'
        self.assertEqual(evaluate_case(row, 'codex', SYNTHETIC_IDENTITY)['status'], 'failed')

    def test_unavailable_is_separate_from_failure(self):
        self.assertEqual(evaluate_case({'case': 'edit', 'unavailable': 'missing binary'}, 'codex', SYNTHETIC_IDENTITY)['status'], 'unavailable')

    def test_copy_hash_checked_before_executing_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'changing-binary'
            path.write_text('installed binary')
            with patch('probe_development.native_binary', return_value=path), \
                 patch('probe_development.sha', side_effect=['a' * 64, 'b' * 64]), \
                 patch('probe_development.subprocess.run') as run:
                with self.assertRaisesRegex(Unavailable, 'changed while making'):
                    checked_native('codex', Path(directory))
                run.assert_not_called()

    def test_existing_receipt_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'receipt.json'
            path.write_text('retained evidence')
            with self.assertRaises(FileExistsError):
                run(SimpleNamespace(output=path))
            self.assertEqual(path.read_text(), 'retained evidence')

    def test_stale_cli_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            supplied = base / 'old-cli'
            supplied.write_bytes(b'old build')
            (base / 'agents-reference').write_bytes(b'fresh build')
            with patch('probe_development.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout='', stderr='')):
                with self.assertRaisesRegex(Unavailable, 'differs from a fresh build'):
                    fresh_cli(supplied, base)

    def test_receipt_retains_integrity_but_rejects_stale_current_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'WORKBENCH/conformance'
            source.mkdir(parents=True)
            for name in (*HELPERS, 'probe_development.py'):
                (source / name).write_text('# synthetic fixture\n')
            receipt = root / 'synthetic.json'
            receipt.with_suffix('.runner.py').write_bytes((source / 'probe_development.py').read_bytes())
            implementation, captured = {}, {}
            for name in source_paths(root):
                destination = root / 'synthetic.artifacts/source' / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes((root / name).read_bytes())
                implementation[name] = sha(destination)
                captured[str(destination.relative_to(root))] = sha(destination)
            record = {'synthetic': True, 'schema_version': '1', 'full_adapter_support': False,
                      'runner_sha256': sha(receipt.with_suffix('.runner.py')),
                      'helper_sha256': {name: sha(source / name) for name in HELPERS},
                      'implementation_sha256': implementation, 'source_artifact_sha256': captured,
                      'source_unchanged': True, 'requested_vendors': ['copilot'], 'vendors': {}}
            receipt.write_text(json.dumps(record))
            first = verify(receipt, root)
            self.assertTrue(first['historical_integrity'])
            self.assertFalse(first['current_workflow_eligible'])
            self.assertEqual(first['current_source_errors'], [])
            (source / 'development_fixture.py').write_text('# changed after capture\n')
            changed = verify(receipt, root)
            self.assertTrue(changed['historical_integrity'])
            self.assertTrue(any('differs' in message for message in changed['current_source_errors']))
            next(iter((root / 'synthetic.artifacts/source').rglob('*.py'))).write_text('corrupt\n')
            self.assertFalse(verify(receipt, root)['historical_integrity'])

    def test_missing_helpers_and_empty_matrix_cannot_qualify(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / 'empty.json'
            receipt.write_text(json.dumps({'schema_version': '1', 'full_adapter_support': False,
                                          'requested_vendors': ['codex'], 'vendors': {'codex': {'cases': []}}}))
            result = verify(receipt, Path(directory))
            self.assertFalse(result['current_workflow_eligible'])
            self.assertIn('required helper comparisons are missing', result['workflow_errors'])
            self.assertIn('codex: incomplete case matrix', result['workflow_errors'])


if __name__ == '__main__':
    unittest.main()
