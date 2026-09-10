#!/usr/bin/env python3
"""Read isolated Codex configuration with the pinned native client; no model turn."""
import argparse
import json
from pathlib import Path
import shutil
import tempfile
import time
from run_native_approvals import Client, PINS, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    binary = shutil.which('codex')
    if not binary or sha(binary) != PINS['codex']:
        parser.error('Codex binary does not match the evidence pin')
    if args.output.exists():
        parser.error('Use a new evidence path')
    base = Path(tempfile.mkdtemp(prefix='oda-config-debug-', dir='/mnt/DATA/tmp'))
    record = {'native_version': '0.154.0', 'binary_sha256': sha(binary),
              'fixture': str(base), 'scope': 'native config load only; no authentication or model turn',
              'full_adapter_support': False, 'cases': []}
    for name, config in {
        'agent-alias': '[agents]\nmax_threads = 2\n',
        'agent-canonical': '[agents]\nmax_concurrent_threads_per_session = 2\n',
        'duplicate-alias': '[agents]\nmax_threads = 2\nmax_concurrent_threads_per_session = 2\n',
        'memory-alias': '[memories]\nno_memories_if_mcp_or_web_search = true\n',
    }.items():
        root, home = base / name / 'workspace', base / name / 'home'
        root.mkdir(parents=True, mode=0o700)
        home.mkdir(mode=0o700)
        (home / 'config.toml').write_text(config)
        (home / 'config.toml').chmod(0o600)
        case = {'name': name, 'config': config, 'loaded': False}
        client = Client([str(Path(binary).resolve()), 'app-server', '--stdio'], root,
                        {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'CODEX_HOME': str(home)}, 'deny', '')
        deadline = time.monotonic() + 15
        try:
            request = client.request('initialize', {'clientInfo': {'name': 'oda_config_debug', 'version': '0.1.0'}})
            client.response(request, deadline)
            client.send({'method': 'initialized', 'params': {}})
            request = client.request('config/read', {'cwd': str(root), 'includeLayers': True})
            case['config_read'] = client.response(request, deadline)
            # Thread creation resolves typed configuration but does not start a turn.
            request = client.request('thread/start', {'cwd': str(root), 'ephemeral': True})
            thread = client.response(request, deadline)
            case['thread_started'] = bool(thread.get('thread', {}).get('id'))
            case['loaded'] = case['thread_started']
        except Exception as error:
            case['error'] = str(error)
        finally:
            client.close()
            case['events'] = client.events
            case['stderr'] = ''.join(client.errors)
        case['warnings'] = [e['params']['summary'] for e in client.events if e.get('method') == 'configWarning']
        effective = case.get('config_read', {}).get('config', {})
        if name == 'duplicate-alias':
            case['matched_expected'] = any('duplicate field' in warning for warning in case['warnings'])
        elif name == 'memory-alias':
            case['matched_expected'] = case['loaded'] and effective.get('memories', {}).get('disable_on_external_context') is True and not case['warnings']
        else:
            case['matched_expected'] = case['loaded'] and effective.get('agents', {}).get('max_concurrent_threads_per_session') == 2 and not case['warnings']
        record['cases'].append(case)
    record['matched_expected_load_results'] = all(case['matched_expected'] for case in record['cases'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2) + '\n')
    args.output.with_suffix('.runner.py').write_bytes(Path(__file__).read_bytes())
    print(json.dumps({'matched_expected_load_results': record['matched_expected_load_results'],
                      'cases': [{'name': c['name'], 'loaded': c['loaded'], 'error': c.get('error')} for c in record['cases']]}))
    return 0 if record['matched_expected_load_results'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
