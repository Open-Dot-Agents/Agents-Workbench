#!/usr/bin/env python3
"""Measure pinned Codex OTLP gRPC and HTTP binary signals on local collectors."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.metadata
import json
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading
import tomllib
import traceback
import urllib.request
import uuid

import grpc
from cryptography import x509
from google.protobuf.json_format import MessageToDict
from opentelemetry.proto.collector.logs.v1 import logs_service_pb2 as logs_pb, logs_service_pb2_grpc as logs_rpc
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2 as traces_pb, trace_service_pb2_grpc as traces_rpc
from opentelemetry.proto.collector.metrics.v1 import metrics_service_pb2 as metrics_pb, metrics_service_pb2_grpc as metrics_rpc

from run_native_approvals import PINS, sha
from run_native_codex_otel_tls import certificate


SIGNALS = {'logs': ('exporter', logs_pb.ExportLogsServiceRequest, logs_pb.ExportLogsServiceResponse),
           'traces': ('trace_exporter', traces_pb.ExportTraceServiceRequest, traces_pb.ExportTraceServiceResponse),
           'metrics': ('metrics_exporter', metrics_pb.ExportMetricsServiceRequest, metrics_pb.ExportMetricsServiceResponse)}


def attributes(values):
    return {a['key']: next(iter(a['value'].values()), None) for a in values}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--transport', choices=['grpc', 'http-binary'], required=True)
    parser.add_argument('--tls', choices=['none', 'ca', 'mutual'], default='none')
    parser.add_argument('--adapter', action='store_true')
    parser.add_argument('--signal', choices=['all', *SIGNALS], default='all')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.transport == 'http-binary' and args.tls == 'mutual':
        parser.error('HTTP identity failures have a separate retained runner')
    output = args.output.resolve()
    snapshot = output.with_suffix('.runner.py')
    assert not output.exists() and not snapshot.exists(), 'refuse evidence replacement'
    dependencies = {p: importlib.metadata.version(p) for p in ['grpcio', 'opentelemetry-proto', 'protobuf', 'cryptography']}
    assert dependencies == {'grpcio': '1.83.1', 'opentelemetry-proto': '1.44.0', 'protobuf': '7.36.1', 'cryptography': '46.0.5'}
    binary = Path('/home/maurizio/.local/bin/codex')
    assert sha(binary) == PINS['codex'], 'native pin mismatch'
    repo = Path(__file__).resolve().parents[2]
    root = Path(tempfile.mkdtemp(prefix='oda-native-otel-transports-', dir='/mnt/DATA/tmp'))
    source, target, workspace, owner, host_home = [root / p for p in ['source', 'target', 'workspace', 'owner', 'host-home']]
    for p in (source, target, workspace, owner, host_home):
        p.mkdir(mode=0o700)
    subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
    tls_dir = source / 'tls'
    tls_dir.mkdir(mode=0o700)
    ca = certificate(tls_dir, 'ca')
    certificate(tls_dir, 'server', ca, server=True)
    client_cert, _ = certificate(tls_dir, 'client', ca)
    private_before = {p.name: {'sha256': sha(p), 'mode': p.stat().st_mode & 0o777} for p in tls_dir.iterdir()}
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(host_home), 'XDG_STATE_HOME': str(root / 'state'),
           'OTEL_BLRP_SCHEDULE_DELAY': '100', 'OTEL_BSP_SCHEDULE_DELAY': '100', 'OTEL_METRIC_EXPORT_INTERVAL': '100'}
    exports, wire = [], []
    result = {'native_version': '0.154.0', 'native_sha256': sha(binary), 'runner_sha256': sha(__file__),
              'transport': args.transport, 'tls': args.tls, 'adapter': args.adapter, 'signal': args.signal,
              'dependencies': dependencies, 'fixture': str(root), 'commands': [], 'phases': [], 'passed': False,
              'helper_sha256': {n: sha(Path(__file__).with_name(n)) for n in ['run_native_approvals.py', 'run_native_codex_otel_tls.py']},
              'implementation_sha256': {str(p.relative_to(repo)): sha(p) for p in sorted((repo / 'CLI/internal/config').glob('*.go'))},
              'source_tls_before': private_before, 'full_adapter_support': False,
              'limitations': ['Isolated user configuration and local model/collectors only.',
                              'No real credentials, remote collector, live reload, or certificate rotation.']}

    def collect(signal, message, metadata, serial):
        entry = {'signal': signal, 'body': MessageToDict(message), 'headers': dict(metadata), 'client_serial': serial}
        if entry['headers'].get('x-oda-control') != 'true':
            exports.append(entry)
        return entry

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            wire.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            rid = 'oda-model-' + str(len(wire))
            item = {'type': 'message', 'role': 'assistant', 'id': rid + '-message',
                    'content': [{'type': 'output_text', 'text': 'Fixture complete.'}]}
            events = [{'type': 'response.created', 'response': {'id': rid}},
                      {'type': 'response.output_item.done', 'item': item},
                      {'type': 'response.completed', 'response': {'id': rid, 'usage': {
                          'input_tokens': 3, 'output_tokens': 2, 'total_tokens': 5,
                          'input_tokens_details': None, 'output_tokens_details': None}}}]
            data = ''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    class Collector(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            signal = self.path.lstrip('/')
            message = SIGNALS[signal][1].FromString(self.rfile.read(int(self.headers['Content-Length'])))
            peer = self.connection.getpeercert(binary_form=True) if args.tls != 'none' else None
            serial = x509.load_der_x509_certificate(peer).serial_number if peer else None
            collect(signal, message, self.headers.items(), serial)
            data = SIGNALS[signal][2]().SerializeToString()
            self.send_response(200)
            self.send_header('Content-Type', 'application/x-protobuf')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    class Service:
        def __init__(self, signal): self.signal = signal
        def Export(self, request, context):
            peer = context.auth_context().get('x509_pem_cert', [])
            serial = x509.load_pem_x509_certificate(peer[0]).serial_number if peer else None
            collect(self.signal, request, context.invocation_metadata(), serial)
            return SIGNALS[self.signal][2]()

    model = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    threading.Thread(target=model.serve_forever, daemon=True).start()
    if args.transport == 'grpc':
        collector = grpc.server(ThreadPoolExecutor(max_workers=4))
        logs_rpc.add_LogsServiceServicer_to_server(Service('logs'), collector)
        traces_rpc.add_TraceServiceServicer_to_server(Service('traces'), collector)
        metrics_rpc.add_MetricsServiceServicer_to_server(Service('metrics'), collector)
        if args.tls == 'none':
            port = collector.add_insecure_port('127.0.0.1:0')
        else:
            credentials = grpc.ssl_server_credentials([((tls_dir / 'server.key').read_bytes(), (tls_dir / 'server.pem').read_bytes())],
                root_certificates=(tls_dir / 'ca.pem').read_bytes(), require_client_auth=args.tls == 'mutual')
            port = collector.add_secure_port('127.0.0.1:0', credentials)
        collector.start()
    else:
        collector = ThreadingHTTPServer(('127.0.0.1', 0), Collector)
        if args.tls != 'none':
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(tls_dir / 'server.pem', tls_dir / 'server.key')
            collector.socket = context.wrap_socket(collector.socket, server_side=True)
        port = collector.server_port
        threading.Thread(target=collector.serve_forever, daemon=True).start()
    endpoint = ('http' if args.tls == 'none' else 'https') + f'://127.0.0.1:{port}'

    def invoke(command, native_home=source, cwd=workspace):
        run = subprocess.run([str(p) for p in command], cwd=cwd, env=None if command[0] == 'go' else dict(env, CODEX_HOME=str(native_home)),
                             capture_output=True, text=True, timeout=45)
        entry = {'command': [str(p) for p in command], 'exit_code': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr}
        result['commands'].append(entry)
        assert run.returncode == 0, json.dumps(entry)
        return entry

    def run_native(home, label):
        nonce = 'ODA_OTLP_' + uuid.uuid4().hex
        command = [binary]
        for setting in ['model="fixture-model"', 'model_provider="fixture"', 'features.enable_request_compression=false',
                        'features.remote_plugin=false', 'features.recommended_plugins=false', 'analytics.enabled=false',
                        'model_providers.fixture.name="Local fixture"', 'model_providers.fixture.wire_api="responses"',
                        'model_providers.fixture.requires_openai_auth=false', 'model_providers.fixture.supports_websockets=false',
                        f'model_providers.fixture.base_url="http://127.0.0.1:{model.server_port}/v1"']:
            command += ['-c', setting]
        command += ['exec', '--json', nonce]
        start, wire_start, before = len(exports), len(wire), sha(home / 'config.toml')
        run = invoke(command, native_home=home)
        events = [json.loads(line) for line in run['stdout'].splitlines() if line.startswith('{')]
        phase = {'label': label, 'events': events, 'exports': exports[start:], 'requests': wire[wire_start:],
                 'native_exit_code': run['exit_code'], 'stderr': run['stderr'], 'config_unchanged': before == sha(home / 'config.toml')}
        result['phases'].append(phase)
        assert any(e['type'] == 'turn.completed' for e in events), 'no completed native turn'
        thread = next(e['thread_id'] for e in events if e['type'] == 'thread.started')
        assert nonce in json.dumps(phase['requests']), 'model prompt missing'
        observed = {e['signal'] for e in phase['exports']}
        phase.update(thread_id=thread, observed_signals=sorted(observed))
        expected = set(SIGNALS) if args.signal == 'all' else {args.signal}
        assert observed == expected, f'signal delivery differs: {sorted(observed)} expected {sorted(expected)}'
        assert phase['config_unchanged'], 'native execution changed configuration'
        assert all(e['headers'].get('x-oda-fixture') == 'transport-probe' for e in phase['exports']), 'configured header missing'
        assert all(e['client_serial'] == (client_cert.serial_number if args.tls == 'mutual' else None) for e in phase['exports']), 'client certificate mismatch'
        records = [log for e in phase['exports'] for r in e['body'].get('resourceLogs', []) for s in r['scopeLogs'] for log in s['logRecords']]
        spans = [span for e in phase['exports'] for r in e['body'].get('resourceSpans', []) for s in r['scopeSpans'] for span in s['spans']]
        metrics = [m for e in phase['exports'] for r in e['body'].get('resourceMetrics', []) for s in r['scopeMetrics'] for m in s['metrics']]
        if records:
            prompts = [attributes(r.get('attributes', [])) for r in records if attributes(r.get('attributes', [])).get('event.name') == 'codex.user_prompt']
            assert prompts and all(p.get('prompt') == '[REDACTED]' and p.get('conversation.id') == thread for p in prompts)
        if records and spans:
            assert {r.get('traceId') for r in records} & {s.get('traceId') for s in spans} - {None}, 'log/trace correlation missing'
        if 'metrics' in expected:
            phase['metric_names'] = sorted({m['name'] for m in metrics})
            assert any(m['name'] == 'codex.api_request' for m in metrics), 'API metric missing'
        phase['correlated'] = True

    try:
        if args.transport == 'grpc':
            if args.tls == 'none':
                channel = grpc.insecure_channel(f'127.0.0.1:{port}')
            else:
                credentials = grpc.ssl_channel_credentials((tls_dir / 'ca.pem').read_bytes(),
                    (tls_dir / 'client.key').read_bytes() if args.tls == 'mutual' else None,
                    (tls_dir / 'client.pem').read_bytes() if args.tls == 'mutual' else None)
                channel = grpc.secure_channel(f'127.0.0.1:{port}', credentials)
            with channel:
                for stub, request in [(logs_rpc.LogsServiceStub, logs_pb.ExportLogsServiceRequest),
                                      (traces_rpc.TraceServiceStub, traces_pb.ExportTraceServiceRequest),
                                      (metrics_rpc.MetricsServiceStub, metrics_pb.ExportMetricsServiceRequest)]:
                    stub(channel).Export(request(), metadata=[('x-oda-control', 'true')], timeout=5)
        else:
            context = ssl.create_default_context(cafile=tls_dir / 'ca.pem') if args.tls != 'none' else None
            for signal in SIGNALS:
                request = urllib.request.Request(endpoint+'/'+signal, data=SIGNALS[signal][1]().SerializeToString(),
                    headers={'Content-Type': 'application/x-protobuf', 'x-oda-control': 'true'})
                with urllib.request.urlopen(request, context=context, timeout=5) as response:
                    assert response.status == 200
        result['independent_collector_control'] = True
        text = '[otel]\nenvironment = "oda-transport-probe"\nlog_user_prompt = false\n'
        for signal, (kind, _, _) in SIGNALS.items():
            if args.signal != 'all' and args.signal != signal:
                text += kind + ' = "none"\n'
        for signal, (kind, _, _) in SIGNALS.items():
            if args.signal != 'all' and args.signal != signal: continue
            transport = 'otlp-grpc' if args.transport == 'grpc' else 'otlp-http'
            text += f'\n[otel.{kind}.{transport}]\nendpoint = '+json.dumps(endpoint if args.transport == 'grpc' else endpoint+'/'+signal)+'\nheaders = {"x-oda-fixture"="transport-probe"}\n'
            if args.transport == 'http-binary': text += 'protocol = "binary"\n'
            if args.tls != 'none':
                text += f'\n[otel.{kind}.{transport}.tls]\nca-certificate = "tls/ca.pem"\n'
                if args.tls == 'mutual': text += 'client-certificate = "tls/client.pem"\nclient-private-key = "tls/client.key"\n'
        (source / 'config.toml').write_text(text)
        (source / 'config.toml').chmod(0o600)
        run_native(source, 'source')
        if args.adapter:
            cli = root / 'agents'
            invoke(['go', 'build', '-trimpath', '-buildvcs=false', '-o', cli, './cmd/agents'], cwd=repo / 'CLI')
            invoke([cli, 'import', '--vendor', 'codex', '--root', owner, '--experimental', '--scope', 'user', '--native-home', source])
            invoke([cli, 'apply', '--vendor', 'codex', '--root', owner, '--experimental', '--scope', 'user', '--native-home', target])
            run_native(target, 'relocated')
            again = root / 'again'
            invoke([cli, 'import', '--vendor', 'codex', '--root', again, '--experimental', '--scope', 'user', '--native-home', target])
            filename = '.agents/native/com.openai.codex/config.toml'
            imported = tomllib.loads((owner / filename).read_text())
            assert imported == tomllib.loads((again / filename).read_text()), 'reimport changed native configuration'
            result['imported_config'] = imported
            assert not any(p.suffix in ('.key', '.pem') for d in (owner, target, again) for p in d.rglob('*')), 'TLS assets were copied'
            assert (target / 'config.toml').stat().st_mode & 0o777 == 0o600
        result['source_tls_after'] = {p.name: {'sha256': sha(p), 'mode': p.stat().st_mode & 0o777} for p in tls_dir.iterdir()}
        assert result['source_tls_after'] == private_before and (source / 'config.toml').read_text() == text
        result['passed'] = True
    except Exception:
        result['error'] = traceback.format_exc()
    finally:
        if args.transport == 'grpc': collector.stop(0).wait(5)
        else:
            collector.shutdown()
            collector.server_close()
        model.shutdown()
        model.server_close()
        output.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(Path(__file__).read_bytes())
        output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({'output': str(output), 'passed': result['passed'], 'error': result.get('error')}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
