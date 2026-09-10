#!/usr/bin/env python3
"""Test Codex telemetry TLS references with a local mutual-TLS collector."""
import argparse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading
import tomllib
import traceback
import uuid
import urllib.request

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from run_native_approvals import PINS, sha


def certificate(directory, name, issuer=None, server=False, key_type='ec'):
    key = ec.generate_private_key(ec.SECP256R1()) if key_type == 'ec' else rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.now(timezone.utc)
    builder = (x509.CertificateBuilder().subject_name(subject)
               .issuer_name(issuer[0].subject if issuer else subject).public_key(key.public_key())
               .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
               .not_valid_after(now + timedelta(days=1))
               .add_extension(x509.BasicConstraints(ca=issuer is None, path_length=None), critical=True))
    if issuer:
        builder = builder.add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH if server else ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
    if server:
        builder = builder.add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
    cert = builder.sign(issuer[1] if issuer else key, hashes.SHA256())
    for suffix, data in [('.pem', cert.public_bytes(serialization.Encoding.PEM)),
                         ('.key', key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))]:
        path = directory / (name + suffix)
        path.write_bytes(data)
        path.chmod(0o600)
    return cert, key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--paths', choices=['relative', 'absolute', 'home'], default='relative')
    parser.add_argument('--adapter', action='store_true')
    parser.add_argument('--tls-mode', choices=['ca', 'mutual'], default='mutual')
    parser.add_argument('--key-type', choices=['ec', 'rsa'], default='ec')
    parser.add_argument('--expect-identity-failure', action='store_true')
    args = parser.parse_args()
    if args.expect_identity_failure and (args.tls_mode != 'mutual' or args.adapter):
        parser.error('identity-failure control uses direct mutual TLS')
    output = args.output.resolve()
    snapshot = output.with_suffix('.runner.py')
    assert not output.exists() and not snapshot.exists(), 'refuse evidence replacement'
    binary = Path('/home/maurizio/.local/bin/codex')
    assert sha(binary) == PINS['codex'], 'native pin mismatch'
    repo = Path(__file__).resolve().parents[2]
    root = Path(tempfile.mkdtemp(prefix='oda-native-codex-otel-tls-', dir='/mnt/DATA/tmp'))
    source, target, workspace, owner, host_home = [root / name for name in ('source', 'target', 'workspace', 'owner', 'host-home')]
    for path in (source, target, workspace, owner, host_home): path.mkdir(mode=0o700)
    subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
    tls_dir = (host_home if args.paths == 'home' else source) / 'tls'
    tls_dir.mkdir(mode=0o700)
    ca = certificate(tls_dir, 'ca', key_type=args.key_type)
    certificate(tls_dir, 'server', ca, server=True, key_type=args.key_type)
    client_cert, _ = certificate(tls_dir, 'client', ca, key_type=args.key_type)
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(host_home), 'XDG_STATE_HOME': str(root / 'state'),
           'OTEL_BLRP_SCHEDULE_DELAY': '100', 'OTEL_BSP_SCHEDULE_DELAY': '100'}
    sources = sorted((repo / 'CLI').rglob('*.go')) + sorted((repo / 'CLI/internal/config/native_schemas').glob('*.json')) + [repo / 'CLI/go.mod', repo / 'CLI/go.sum']
    result = {'fixture': str(root), 'native_version': '0.154.0', 'native_sha256': sha(binary), 'paths': args.paths,
              'runner_sha256': sha(__file__), 'adapter': args.adapter, 'tls_mode': args.tls_mode, 'key_type': args.key_type,
              'expect_identity_failure': args.expect_identity_failure,
              'helper_sha256': {'run_native_approvals.py': sha(Path(__file__).with_name('run_native_approvals.py'))},
              'implementation_sha256': {str(p.relative_to(repo)): sha(p) for p in sources},
              'commands': [], 'phases': [], 'full_adapter_support': False,
              'limitations': ['Local mutual TLS with OTLP HTTP JSON logs and traces only.',
                              'No gRPC, binary encoding, metrics, certificate rotation, or live reload claim.',
                              'Only generated fixture credentials are used; private key bytes are not included in evidence.']}
    wire, exports = [], []

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_): pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            wire.append(request)
            rid = 'fixture-' + str(len(wire))
            item = {'type': 'message', 'role': 'assistant', 'id': rid + '-message',
                    'content': [{'type': 'output_text', 'text': 'Fixture complete.'}]}
            events = [{'type': 'response.created', 'response': {'id': rid}},
                      {'type': 'response.output_item.done', 'item': item},
                      {'type': 'response.completed', 'response': {'id': rid, 'usage': {
                          'input_tokens': 0, 'input_tokens_details': None, 'output_tokens': 0,
                          'output_tokens_details': None, 'total_tokens': 0}}}]
            body = ''.join('event: ' + e['type'] + '\ndata: ' + json.dumps(e) + '\n\n' for e in events).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    class Collector(BaseHTTPRequestHandler):
        def log_message(self, *_): pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            peer = self.connection.getpeercert(binary_form=True)
            serial = x509.load_der_x509_certificate(peer).serial_number if peer else None
            exports.append({'path': self.path, 'body': body, 'client_serial': serial})
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'{}')

    model = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    collector = ThreadingHTTPServer(('127.0.0.1', 0), Collector)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tls_dir / 'server.pem', tls_dir / 'server.key')
    context.load_verify_locations(tls_dir / 'ca.pem')
    context.verify_mode = ssl.CERT_REQUIRED if args.tls_mode == 'mutual' else ssl.CERT_NONE
    collector.socket = context.wrap_socket(collector.socket, server_side=True)
    for server in (model, collector): threading.Thread(target=server.serve_forever, daemon=True).start()

    def invoke(command, cwd=workspace, native_home=source, check=True):
        run = subprocess.run([str(p) for p in command], cwd=cwd, env=None if command[0] == 'go' else dict(env, CODEX_HOME=str(native_home)),
                             capture_output=True, text=True, timeout=45)
        record = {'command': [str(p) for p in command], 'exit_code': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr}
        result['commands'].append(record)
        if check: assert run.returncode == 0, json.dumps(record)
        return record

    def run_native(home, label):
        nonce = 'ODA_TLS_PROMPT_' + uuid.uuid4().hex
        command = [binary]
        for setting in ['model="fixture-model"', 'model_provider="fixture"', 'features.enable_request_compression=false',
                        'model_providers.fixture.name="Local fixture"', 'model_providers.fixture.wire_api="responses"',
                        'model_providers.fixture.requires_openai_auth=false', 'model_providers.fixture.supports_websockets=false',
                        f'model_providers.fixture.base_url="http://127.0.0.1:{model.server_port}/v1"']:
            command += ['-c', setting]
        command += ['exec', '--json', nonce]
        start, wire_start = len(exports), len(wire)
        before = sha(home / 'config.toml')
        run = invoke(command, native_home=home, check=False)
        events = [json.loads(line) for line in run['stdout'].splitlines() if line.startswith('{')]
        phase = {'label': label, 'events': events, 'exports': exports[start:], 'requests': wire[wire_start:],
                 'native_exit_code': run['exit_code'], 'stderr': run['stderr'], 'config_unchanged': before == sha(home / 'config.toml')}
        result['phases'].append(phase)
        assert run['exit_code'] == 0 and any(e['type'] == 'turn.completed' for e in events), 'native turn failed'
        thread = next(e['thread_id'] for e in events if e['type'] == 'thread.started')
        assert nonce in json.dumps(phase['requests']), 'model did not receive fixture prompt'
        if args.expect_identity_failure:
            assert not phase['exports'] and 'Could not create otel exporter: builder error' in phase['stderr'], 'native identity limitation changed'
            assert phase['config_unchanged'], 'native execution changed configuration'
            phase.update(native_identity_failure=True, thread_id=thread)
            return
        logs = [e for e in phase['exports'] if e['path'] == '/logs']
        traces = [e for e in phase['exports'] if e['path'] == '/traces']
        assert logs and traces and thread in json.dumps(logs), 'TLS delivery or thread correlation failed'
        records = [log for e in logs for r in e['body']['resourceLogs'] for s in r['scopeLogs'] for log in s['logRecords']]
        spans = [span for e in traces for r in e['body']['resourceSpans'] for s in r['scopeSpans'] for span in s['spans']]
        assert {r['traceId'] for r in records} & {s['traceId'] for s in spans}, 'TLS logs and spans do not correlate'
        assert all(e['client_serial'] == (client_cert.serial_number if args.tls_mode == 'mutual' else None) for e in phase['exports']), 'wrong client certificate'
        assert phase['config_unchanged'], 'native execution changed configuration'
        phase.update(correlated=True, client_serial=client_cert.serial_number)

    try:
        control_context = ssl.create_default_context(cafile=tls_dir / 'ca.pem')
        if args.tls_mode == 'mutual':
            control_context.load_cert_chain(tls_dir / 'client.pem', tls_dir / 'client.key')
        request = urllib.request.Request(f'https://127.0.0.1:{collector.server_port}/control', data=b'{}', headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, context=control_context, timeout=5) as response:
            assert response.status == 200 and response.read() == b'{}'
        result['collector_control'] = exports[-1]
        assert exports[-1]['client_serial'] == (client_cert.serial_number if args.tls_mode == 'mutual' else None), 'independent TLS control failed'
        paths = {key: (str(tls_dir / name) if args.paths == 'absolute' else ('~/' if args.paths == 'home' else '') + 'tls/' + name)
                 for key, name in [('ca-certificate', 'ca.pem'), ('client-certificate', 'client.pem'), ('client-private-key', 'client.key')]}
        if args.tls_mode == 'ca': paths = {'ca-certificate': paths['ca-certificate']}
        text = '[otel]\nmetrics_exporter = "none"\nlog_user_prompt = false\n'
        for kind, suffix in [('exporter', 'logs'), ('trace_exporter', 'traces')]:
            text += f'[otel.{kind}.otlp-http]\nendpoint = "https://127.0.0.1:{collector.server_port}/{suffix}"\nprotocol = "json"\n'
            text += f'[otel.{kind}.otlp-http.tls]\n' + ''.join(key + ' = ' + json.dumps(value) + '\n' for key, value in paths.items())
        (source / 'config.toml').write_text(text)
        (source / 'config.toml').chmod(0o600)
        before = {str(p.relative_to(root)): {'sha256': sha(p), 'mode': p.stat().st_mode & 0o777} for p in tls_dir.iterdir()}
        result.update(source_tls_before=before, references=paths)
        run_native(source, 'source')
        if args.adapter:
            cli = root / 'agents'
            invoke(['go', 'build', '-o', cli, './cmd/agents'], cwd=repo / 'CLI')
            invoke([cli, 'import', '--vendor', 'codex', '--root', owner, '--scope', 'user', '--native-home', source, '--experimental'])
            invoke([cli, 'apply', '--vendor', 'codex', '--root', owner, '--scope', 'user', '--native-home', target, '--experimental'])
            run_native(target, 'relocated')
            again = root / 'again'
            again.mkdir()
            invoke([cli, 'import', '--vendor', 'codex', '--root', again, '--scope', 'user', '--native-home', target, '--experimental'])
            filename = '.agents/native/com.openai.codex/config.toml'
            imported = tomllib.loads((owner / filename).read_text())
            assert imported == tomllib.loads((again / filename).read_text()), 'reimport changed references'
            result['imported_config'] = imported
            assert not any(p.suffix in ('.key', '.pem') for directory in (owner, target, again) for p in directory.rglob('*')), 'private TLS assets were copied'
            assert (target / 'config.toml').stat().st_mode & 0o777 == 0o600, 'target is not private'
        result['source_tls_after'] = {str(p.relative_to(root)): {'sha256': sha(p), 'mode': p.stat().st_mode & 0o777} for p in tls_dir.iterdir()}
        assert result['source_tls_after'] == before and (source / 'config.toml').read_text() == text, 'source configuration or TLS files changed'
        result['passed'] = True
    except Exception:
        result.update(passed=False, error=traceback.format_exc())
    finally:
        for server in (model, collector):
            server.shutdown()
            server.server_close()
        snapshot.write_bytes(Path(__file__).read_bytes())
        output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'output': str(output), 'passed': result['passed'], 'error': result.get('error')}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
