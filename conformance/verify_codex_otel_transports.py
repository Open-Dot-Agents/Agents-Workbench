#!/usr/bin/env python3
"""Verify the bounded Codex OTLP transport and credential-exclusion records."""
import argparse
import hashlib
import json
from pathlib import Path

from evidence_state import assess_receipt

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'WORKBENCH/evidence/native-draft2-debug'
CASES = {
    'grpc': ('grpc', 'none', 'on', 'none'),
    'grpc-ca': ('grpc', 'ca', 'on', 'none'),
    'grpc-mutual': ('grpc', 'mutual', 'on', 'none'),
    'binary': ('http-binary', 'none', 'on', 'none'),
    'binary-ca': ('http-binary', 'ca', 'on', 'none'),
    'grpc-analytics-off': ('grpc', 'none', 'off', 'none'),
    'auth-preservation': ('http-binary', 'none', 'on', 'inline'),
    'grpc-auth-preservation': ('grpc', 'none', 'on', 'inline'),
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def attrs(record):
    return {a['key']: next(iter(a['value'].values()), None) for a in record.get('attributes', [])}


def verify(evidence_suffix='project-skills-final'):
    eligibility = []
    for name, settings in CASES.items():
        path = BASE / ('codex-otel-' + name + '-' + evidence_suffix + '.json')
        record = json.loads(path.read_text())
        assert record['passed'] and record['adapter'] and record['signal'] == 'all', name
        assert tuple(record[k] for k in ('transport', 'tls', 'analytics', 'credentials')) == settings
        assert record['native_version'] == '0.154.0'
        assert record['native_sha256'] == '3188814c35471432d4123203e0eb38e5bddc60226e3d7ddf0e59e649ea140022'
        assert record['runner_sha256'] == sha(path.with_suffix('.runner.py'))
        assert record['dependencies'] == {'grpcio': '1.83.1', 'opentelemetry-proto': '1.44.0', 'protobuf': '7.36.1', 'cryptography': '46.0.5'}
        state = assess_receipt(path, ROOT, current_runner=ROOT/'WORKBENCH/conformance/run_native_codex_otel_transports.py',
                               current_helpers={helper: ROOT/'WORKBENCH/conformance'/helper for helper in record['helper_sha256']})
        assert state.integrity_valid, state.integrity_errors
        eligibility.append(state.current_eligible)
        assert record['independent_collector_control'] and not record['full_adapter_support']
        assert record['source_tls_before'] == record['source_tls_after']
        assert 'ODA_OTLP_SYNTHETIC' not in json.dumps(record), 'credential value in evidence'
        assert [p['label'] for p in record['phases']] == ['source', 'relocated']
        client_serials = set()
        for phase in record['phases']:
            assert phase['correlated'] and phase['config_unchanged'] and phase['native_exit_code'] == 0
            assert any(e['type'] == 'turn.completed' for e in phase['events'])
            assert any(e['type'] == 'thread.started' and e['thread_id'] == phase['thread_id'] for e in phase['events'])
            expected = {'logs', 'traces', 'metrics'}
            if record['analytics'] == 'off': expected.remove('metrics')
            if record['credentials'] == 'inline' and phase['label'] == 'relocated': expected = set()
            assert set(phase['observed_signals']) == expected == {e['signal'] for e in phase['exports']}
            requests = [r for r in phase['requests'] if r['path'].endswith('/responses')]
            assert requests and len(requests) == phase['model_response_requests']
            assert all(r['body']['model'] == 'fixture-model' for r in requests)
            for export in phase['exports']:
                assert export['headers'].get('x-oda-fixture') == 'transport-probe'
                assert export['authorized'] == (record['credentials'] == 'inline')
                if record['tls'] == 'mutual':
                    assert isinstance(export['client_serial'], int) and export['client_serial'] > 0
                    client_serials.add(export['client_serial'])
                else:
                    assert export['client_serial'] is None
                for key in ('resourceLogs', 'resourceSpans', 'resourceMetrics'):
                    for resource in export['body'].get(key, []):
                        assert attrs(resource['resource'])['env'] == 'oda-transport-probe'
            logs = [l for e in phase['exports'] for r in e['body'].get('resourceLogs', []) for s in r['scopeLogs'] for l in s['logRecords']]
            spans = [s for e in phase['exports'] for r in e['body'].get('resourceSpans', []) for group in r['scopeSpans'] for s in group['spans']]
            if expected:
                prompts = [attrs(l) for l in logs if attrs(l).get('event.name') == 'codex.user_prompt']
                assert prompts and all(p['prompt'] == '[REDACTED]' and p['conversation.id'] == phase['thread_id'] for p in prompts)
                assert {l.get('traceId') for l in logs} & {s.get('traceId') for s in spans} - {None}
            if 'metrics' in expected:
                metrics = [m for e in phase['exports'] for r in e['body'].get('resourceMetrics', []) for s in r['scopeMetrics'] for m in s['metrics']]
                points = [p for m in metrics if m['name'] == 'codex.api_request' for p in m['sum']['dataPoints']]
                total = sum(int(p.get('asInt', 0)) for p in points if attrs(p).get('model') == 'fixture-model' and attrs(p).get('status') == '200' and attrs(p).get('success') == 'true')
                assert total == len(requests) == phase['api_request_count']
        if record['tls'] == 'mutual': assert len(client_serials) == 1
        if record['credentials'] == 'inline':
            assert all(record['imported_config']['otel'][k] == 'none' for k in ('exporter', 'trace_exporter', 'metrics_exporter'))
    before = json.loads((BASE / 'codex-otel-auth-preservation-before.json').read_text())
    assert not before['passed'] and 'exporter lost authentication' in before['error']
    assert all(e['authorized'] for e in before['phases'][0]['exports'])
    assert before['phases'][1]['exports'] and not any(e['authorized'] for e in before['phases'][1]['exports'])
    regression = json.loads((BASE / 'codex-otel-auth-regression-corrected-before.json').read_text())
    assert regression['exit_code'] != 0 and 'credential removal left the exporter active' in regression['stdout']
    first = json.loads((BASE / 'codex-otel-grpc-first.json').read_text())
    assert not first['passed'] and 'signal delivery differs' in first['error']
    sources = json.loads((BASE / 'codex-otel-transports.sources.json').read_text())
    assert sources['codex_source_revision'] == '6b9826e3aa83b1a5947db50f4332cb9c65f1b340'
    for source in sources['sources']:
        assert sha(BASE / source['file']) == source['sha256']
    return {'passed': True, 'native_cases': len(CASES), 'native_phases': 2 * len(CASES), 'scope': 'user',
            'historical_integrity': True, 'current_support_eligible': all(eligibility), 'full_adapter_support': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-suffix', choices=['project-skills-final', 'project-skills', 'skill-rechecked', 'skill-current', 'final', 'auth-current', 'command-current', 'scope-current', 'role-current', 'role-checked', 'reference-current'], default='project-skills-final')
    print(json.dumps(verify(parser.parse_args().evidence_suffix), indent=2))
