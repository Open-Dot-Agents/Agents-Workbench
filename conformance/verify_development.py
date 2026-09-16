"""Verify development workflow outcomes and their captured source provenance."""
import argparse
import hashlib
import json
from pathlib import Path
import re

from development_cases import CODEX_CASES, OBSERVATIONS, cases_for, expectations
from development_fixture import USER_FILES, VENDORS, parse_native_version
from evidence_state import assess_receipt

ROOT = Path(__file__).resolve().parents[2]
HELPERS = ('development_fixture.py', 'development_cases.py', 'verify_development.py',
           'run_native_approvals.py', 'evidence_state.py', 'check_clean_source.py')


def source_paths(root):
    """Include implementation, schemas, fixtures, and deterministic gate code."""
    paths = []
    for component in ('CLI', 'SPEC', 'WORKBENCH', '.agents'):
        for path in (root / component).rglob('*'):
            relative = path.relative_to(root)
            if any(part in ('.git', '__pycache__', 'evidence', 'node_modules', 'state') for part in relative.parts):
                continue
            if path.is_file() and not path.is_symlink() and (component == '.agents' or path.suffix in
                    ('.go', '.py', '.json', '.yaml', '.yml', '.toml', '.mod', '.sum', '.sh', '.md', '.txt')):
                paths.append(str(relative))
    return sorted(paths)

CHECKS = {
    'new': {'repeat-actions', 'repeat-bytes'}, 'adopt': {'manifest-backup'},
    'update': set(), 'conflict': {'conflict-preserved'}, 'remove': {'removal-repeat'},
    'rollback': {'rollback-driver', 'rollback-restored', 'rollback-injected-after-write'},
    'legacy-migration': {'legacy-backup', 'legacy-keys-removed', 'unrelated-model', 'unrelated-mcp'},
    'relocate': {'original-project-preserved'},
}


def session_errors(session, vendor, identity):
    errors = []
    label = session.get('label')
    present, absent = expectations(label)
    if session.get('expected_present') != list(present) or session.get('expected_absent') != list(absent):
        errors.append('guidance expectations differ from the case contract')
    requests = session.get('requests', [])
    if (not identity.get('native_sha256') or session.get('native_sha256') != identity['native_sha256']
            or not identity.get('native_version') or session.get('native_version') != identity['native_version']):
        errors.append('native binary was not checked before this session')
    nonce, identifier = session.get('nonce'), session.get('session_id')
    if (session.get('error') or session.get('provider_errors') or session.get('process_exited') is not True
            or not nonce or not identifier or not requests):
        return errors + ['native session did not complete cleanly']
    context = json.dumps(requests[0])
    if nonce not in context or any(marker not in context for marker in present) or any(marker in context for marker in absent):
        errors.append('native prompt does not contain the expected current guidance')
    completion = session.get('completion', {})
    if vendor == 'codex':
        thread = session.get('thread', {})
        if (thread.get('thread', {}).get('id') != identifier or thread.get('approvalPolicy') != 'on-request'
                or completion.get('threadId') != identifier or completion.get('turn', {}).get('status') != 'completed'):
            errors.append('uncorrelated Codex completion or approval policy')
        if label not in ('remove', 'host-restriction') and thread.get('activePermissionProfile', {}).get('id') != 'agents-development':
            errors.append('development permission profile was not active')
        model = session.get('effective_config', {}).get('config', {}).get('model')
        expected_model = 'fixture-project' if label in ('legacy-migration', 'global-override') else 'fixture-global'
        if model != expected_model:
            errors.append('native model override/fallback differs')
    elif completion.get('stopReason') != 'end_turn':
        errors.append('Copilot turn did not end')
    if not any(nonce + '_DONE' in json.dumps(event) and identifier in json.dumps(event) for event in session.get('events', [])):
        errors.append('native response marker is missing or uncorrelated')
    if not session.get('command') and session.get('approvals'):
        errors.append('passive native phase unexpectedly requested approval')
    return errors


def tool_result(session):
    """Read only the output correlated with the command emitted to this turn."""
    requests = session.get('requests', [])
    if len(requests) < 2:
        return None, '', False
    items = requests[-1].get('input', [])
    calls = [item for item in items if item.get('type') == 'function_call' and item.get('call_id') == 'development-call']
    outputs = [item for item in items if item.get('type') == 'function_call_output' and item.get('call_id') == 'development-call']
    if len(calls) != 1 or len(outputs) != 1 or calls[0].get('name') != 'exec_command':
        return None, '', False
    try:
        arguments = json.loads(calls[0]['arguments'])
    except (KeyError, TypeError, ValueError):
        return None, '', False
    if arguments != {'cmd': session.get('command'), 'workdir': session.get('cwd'), 'login': False}:
        return None, '', False
    output = outputs[0].get('output', '')
    if not isinstance(output, str):
        return None, '', False
    match = re.search(r'(?m)^Process exited with code ([0-9]+)$', output)
    return int(match[1]) if match else None, output, True


def evaluate_case(row, vendor, identity):
    name = row.get('case')
    errors = []
    if row.get('error'):
        errors.append(row['error'])
    if row.get('unavailable'):
        return {'status': 'unavailable', 'errors': [row['unavailable']]}
    checks = row.get('checks', [])
    wanted = CHECKS.get(name, set())
    if name == 'global-scope':
        wanted = {'project-policy-user-refused', 'user-files-preserved'}
        if vendor == 'codex':
            wanted |= {'scoped-repeat-actions', 'scoped-preserves-ownership', 'full-requirement-refusal'}
    if {item.get('id') for item in checks} != wanted or len(checks) != len(wanted):
        errors.append('required lifecycle assertions are missing or duplicated')
    if any('actual' not in item or 'expected' not in item or item['actual'] != item['expected'] for item in checks):
        errors.append('lifecycle assertion failed')
    commands = row.get('commands', [])
    if (not commands and name != 'rollback') or any((item.get('exit_code') == 0) != item.get('expect_success')
                           or not item.get('authority_before') or item['authority_before'] != item.get('authority_after')
                           for item in commands):
        errors.append('CLI command failed or changed fixture host authority')
    if name == 'global-scope':
        project_commands = [item for item in commands if '--global' not in item.get('command', [])]
        if not project_commands or any(set(item.get('user_files_before', {})) != set(USER_FILES)
                or item.get('user_files_before') != item.get('user_files_after') for item in project_commands):
            errors.append('project commands did not preserve user files')
    labels = [name]
    if name == 'global-scope' and vendor == 'codex':
        labels += ['global-override', 'global-fallback']
    if name == 'global-scope' and vendor == 'copilot':
        labels += ['copilot-global-override', 'copilot-global-fallback']
    if name == 'protected-later':
        labels = ['protected-later-before', name]
    sessions = row.get('sessions', [])
    if [item.get('label') for item in sessions] != labels:
        errors.append('native lifecycle phases are missing or reordered')
    if len({item.get('session_id') for item in sessions}) != len(sessions):
        errors.append('lifecycle phase did not use a new native session')
    for session in sessions:
        errors.extend(session_errors(session, vendor, identity))
    if name in CODEX_CASES and name != 'legacy-migration' and (not sessions or not sessions[-1].get('command')):
        errors.append('required native operation was not requested')
    if name == 'rollback':
        rollback = row.get('rollback', {})
        if not rollback.get('before') or rollback.get('before') != rollback.get('after') or rollback.get('injected') is not True:
            errors.append('injected rollback did not restore the fixture')
    if name == 'protected-later':
        empty = hashlib.sha256(b'').hexdigest()
        content = hashlib.sha256(b'AGENTS_PRIVATE_SENTINEL\n').hexdigest()
        creation = row.get('protected_later_creation', {})
        before = creation.get('before')
        if row.get('missing_path_before') != {'future.env': None}:
            errors.append('protected path was not initially absent')
        if creation.get('method') == 'create-new-file':
            valid = before is None and row.get('missing_path_after') == {'future.env': None}
        elif creation.get('method') == 'replace-empty-native-placeholder':
            valid = (isinstance(before, dict) and before.get('regular_file') is True and before.get('size') == 0
                     and before.get('owned_by_fixture_user') is True and before.get('links') == 1
                     and isinstance(before.get('mode'), str) and row.get('missing_path_after') == {'future.env': empty})
        else:
            valid = False
        if not valid or creation.get('after') != {'future.env': content}:
            errors.append('later file creation and native placeholder observations are incomplete')
    if sessions and sessions[-1].get('command'):
        session = sessions[-1]
        code, output, correlated = tool_result(session)
        if not correlated:
            errors.append('command result is missing or uncorrelated')
        elif name not in OBSERVATIONS:
            if name.startswith('protected-') or name in ('network', 'host-restriction'):
                denial = code is not None and code != 0 and any(term in output.lower() for term in
                    ('permission denied', 'operation not permitted', 'read-only file system', 'network is unreachable', 'connection refused'))
                if not denial or session.get('approvals'):
                    errors.append('native boundary denial was not demonstrated')
            elif code != 0 or session.get('approvals'):
                errors.append('automatic local operation did not succeed without approval')
        if name in ('protected-read', 'protected-alias', 'protected-later') and 'AGENTS_PRIVATE_SENTINEL' in json.dumps(session.get('requests')):
            errors.append('protected contents reached the model')
        if name in ('edit', 'commit', 'submodule-commit') and row.get('file_after') != 'after\n':
            errors.append('expected file effect is missing')
        if name == 'host-restriction' and row.get('file_after') != 'before\n':
            errors.append('host read-only restriction did not prevent the write')
        if name == 'host-restriction' and (session.get('host_read_only') is not True
                or session.get('thread', {}).get('sandbox', {}).get('type') != 'readOnly'
                or session.get('diagnosis', {}).get('host_override') != 'read-only'):
            errors.append('effective host restriction and diagnosis are missing')
        if name in ('commit', 'submodule-commit') and (not row.get('head_before') or row.get('head_before') == row.get('head_after')):
            errors.append('local commit effect is missing')
        if name == 'build' and row.get('built') is not True:
            errors.append('build artifact is missing')
        if name == 'format' and row.get('formatted') != '{\n    "fixture": true\n}\n':
            errors.append('formatted artifact is incorrect')
        if name == 'test' and ('Ran 1 test' not in output or '\nOK' not in output):
            errors.append('fixture test result is missing')
        if name == 'network' and (row.get('network_requests') != [] or row.get('network_before_control') != 'fixture-network-ok'
                                  or row.get('network_after_control') != 'fixture-network-ok'):
            errors.append('network denial lacks two successful direct controls')
    if name in OBSERVATIONS and row.get('guidance_only') is not True:
        errors.append('operation guidance was mislabeled as enforcement')
    if name in OBSERVATIONS and (type(row.get('published')) is not bool or type(row.get('discarded')) is not bool
                                or not row.get('head_before') or not row.get('head_after')):
        errors.append('operation effects were not recorded')
    return {'status': 'failed' if errors else ('observed' if name in OBSERVATIONS else 'passed'), 'errors': errors}


def verify(path, root=ROOT):
    record = json.loads(path.read_text())
    helpers = {name: root / 'WORKBENCH/conformance' / name for name in record.get('helper_sha256', {})}
    state = assess_receipt(path, root, current_runner=root / 'WORKBENCH/conformance/probe_development.py', current_helpers=helpers)
    errors = []
    unavailable = list(record.get('unavailable', []))
    source_errors = list(state.eligibility_errors)
    implementation = record.get('implementation_sha256', {})
    if set(implementation) != set(source_paths(root)):
        source_errors.append('current source inventory differs from the captured inventory')
    if set(record.get('helper_sha256', {})) != set(HELPERS):
        errors.append('required helper comparisons are missing')
    captured = record.get('source_artifact_sha256', {})
    prefix = path.with_suffix('.artifacts').name + '/source/'
    if captured != {prefix + name: digest for name, digest in implementation.items()}:
        errors.append('source snapshots do not cover the implementation inventory')
    if record.get('source_unchanged') is not True:
        errors.append('source changed during the campaign')
    if record.get('clean_source', {}).get('exit_code') != 0 and ('clean_source' in record or not unavailable):
        errors.append('clean-source checks did not pass')
    if not record.get('fixture_sha256') and not unavailable:
        errors.append('fixture snapshots are missing')
    if record.get('schema_version') != '1' or record.get('full_adapter_support') is not False:
        errors.append('invalid workflow receipt or adapter support claim')
    vendors = record.get('vendors', {})
    expected = record.get('requested_vendors')
    if (expected not in (['codex'], ['copilot'], ['codex', 'copilot'])
            or set(vendors) - set(expected) or (set(vendors) != set(expected) and not unavailable)):
        errors.append('requested vendor results are missing')
    for vendor, result in vendors.items():
        if vendor not in VENDORS:
            errors.append('unknown vendor')
            continue
        if result.get('status') == 'unavailable' and result.get('reason') and not result.get('cases'):
            if result['reason'] not in unavailable:
                unavailable.append(result['reason'])
            continue
        if (not re.fullmatch('[0-9a-f]{64}', result.get('native_sha256', ''))
                or not result.get('native_version') or result.get('version_exit_code') != 0
                or parse_native_version(vendor, result.get('version_output', '')) != result['native_version']):
            errors.append(vendor + ': native identity mismatch')
        rows = result.get('cases', [])
        if [row.get('case') for row in rows] != list(cases_for(vendor)):
            errors.append(vendor + ': incomplete case matrix')
        for row in rows:
            outcome = evaluate_case(row, vendor, result)
            if outcome['status'] == 'unavailable':
                unavailable.extend(message for message in outcome['errors'] if message not in unavailable)
            elif outcome['status'] not in ('passed', 'observed'):
                errors.append(vendor + '/' + str(row.get('case')) + ': ' + '; '.join(outcome['errors']))
    build = record.get('cli_build', {})
    if (record.get('synthetic') or ((build or not unavailable) and (build.get('matches_supplied') is not True
            or not build.get('supplied_sha256') or build.get('reference_sha256') != build.get('supplied_sha256')))):
        errors.append('receipt does not identify a fresh native-tested CLI')
    result = {'historical_integrity': state.integrity_valid, 'integrity_errors': list(state.integrity_errors),
              'workflow_status': 'failed' if errors else ('unavailable' if unavailable else 'passed'),
              'workflow_checks_passed': not errors and not unavailable, 'workflow_errors': errors,
              'current_workflow_eligible': state.current_eligible and not errors and not unavailable and not source_errors,
              'current_source_errors': source_errors, 'unavailable': unavailable, 'full_adapter_support': False}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('receipt', type=Path)
    parser.add_argument('--require-current', action='store_true')
    args = parser.parse_args()
    result = verify(args.receipt.resolve())
    print(json.dumps(result, indent=2))
    return 0 if result['historical_integrity'] and result['workflow_checks_passed'] and (
        not args.require_current or result['current_workflow_eligible']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
