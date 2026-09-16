"""Disposable native sessions for development workflow verification.

The local model emits fixed responses. These sessions measure configuration
and tool boundaries, never model compliance with operation instructions.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import stat
import subprocess
import threading
import time
import uuid

from run_native_approvals import Client, sha

VENDORS = ('codex', 'copilot')
USER_FILES = ('config.toml', 'config.json', 'settings.json', 'permissions-config.json',
              'rules/existing.rules', 'AGENTS.md', 'copilot-instructions.md')


def parse_native_version(vendor, output):
    prefix = {'codex': 'codex-cli ', 'copilot': 'GitHub Copilot CLI '}[vendor]
    version = r'([0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?(?:\+[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?)'
    match = re.search(r'^' + re.escape(prefix) + version + r'\.?[ \t]*$', output, re.MULTILINE)
    return match[1] if match else None


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


def git(root, *arguments):
    return subprocess.check_output(
        ['git', '-C', str(root), *arguments], text=True, stderr=subprocess.STDOUT,
        env={'PATH': '/usr/bin:/bin', 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1'},
    ).strip()


def snapshot(root, paths):
    result = {}
    for name in paths:
        path = root / name
        if path.is_symlink():
            result[name] = {'symlink': os.readlink(path)}
        elif path.is_file():
            result[name] = sha(path)
        elif path.is_dir():
            result[name] = 'directory'
        else:
            result[name] = None
    return result


def inspection_snapshot(root):
    """Fixture-only hashes and metadata; reads can change atime, not mtime."""
    result = {}
    for path in [root, *sorted(root.rglob('*'))]:
        info = path.lstat()
        value = {'mode': info.st_mode, 'mtime_ns': info.st_mtime_ns, 'ctime_ns': info.st_ctime_ns,
                 'uid': info.st_uid, 'gid': info.st_gid}
        if stat.S_ISLNK(info.st_mode):
            value['link'] = os.readlink(path)
        elif stat.S_ISREG(info.st_mode):
            value['sha256'] = sha(path)
        result[str(path.relative_to(root))] = value
    return result


class DevelopmentFixture:
    def __init__(self, base, vendor, binary, agents_cli, native_identity=None):
        self.base, self.vendor, self.binary, self.cli = base, vendor, binary, agents_cli
        self.native_identity = native_identity or {}
        self.home, self.workspace = base / 'home', base / 'workspace'
        self.native = self.home / ('.' + vendor)
        for directory in (self.native, self.workspace):
            directory.mkdir(parents=True, mode=0o700)
        git(self.workspace, '-c', 'init.templateDir=', 'init', '-q')
        git(self.workspace, 'config', 'user.name', 'Development Fixture')
        git(self.workspace, 'config', 'user.email', 'fixture@example.invalid')
        (self.workspace / 'tracked.txt').write_text('before\n')
        git(self.workspace, 'add', 'tracked.txt')
        git(self.workspace, 'commit', '-qm', 'fixture base')
        self.env = {'PATH': '/usr/bin:/bin', 'HOME': str(self.home),
                    'XDG_STATE_HOME': str(base / 'state'), 'GIT_CONFIG_GLOBAL': '/dev/null',
                    'GIT_CONFIG_NOSYSTEM': '1', vendor.upper() + '_BIN': str(binary)}
        self.commands, self.sessions = [], []
        self.requests, self.provider_errors, self.network_requests = [], [], []
        self.command, self.nonce = None, ''
        fixture = self

        class Model(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                fixture.network_requests.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'fixture-network-ok')

            def do_POST(self):
                try:
                    body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    fixture.requests.append(body)
                    identity = 'fixture-' + str(len(fixture.requests))
                    if vendor == 'codex':
                        if fixture.command and len(fixture.requests) == 1:
                            item = {'type': 'function_call', 'call_id': 'development-call',
                                    'name': 'exec_command', 'arguments': json.dumps({
                                        'cmd': fixture.command, 'workdir': str(fixture.workspace), 'login': False})}
                        else:
                            item = {'type': 'message', 'role': 'assistant', 'id': identity + '-message',
                                    'content': [{'type': 'output_text', 'text': fixture.nonce + '_DONE'}]}
                        events = [{'type': 'response.created', 'response': {'id': identity}},
                                  {'type': 'response.output_item.done', 'item': item},
                                  {'type': 'response.completed', 'response': {'id': identity,
                                   'usage': {'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2}}}]
                        payload = ''.join('event: ' + e['type'] + '\ndata: ' + json.dumps(e) + '\n\n' for e in events)
                    else:
                        chunks = [
                            {'id': identity, 'object': 'chat.completion.chunk', 'created': 1,
                             'model': body['model'], 'choices': [{'index': 0, 'delta': {
                                 'role': 'assistant', 'content': fixture.nonce + '_DONE'}, 'finish_reason': None}]},
                            {'id': identity, 'object': 'chat.completion.chunk', 'created': 1,
                             'model': body['model'], 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}],
                             'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}},
                        ]
                        payload = ''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in chunks) + 'data: [DONE]\n\n'
                    encoded = payload.encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.send_header('Content-Length', str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                except Exception as error:
                    fixture.provider_errors.append(str(error))

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/v1'
        if vendor == 'codex':
            self.env['CODEX_HOME'] = str(self.native)
            self.authority = self.native / 'config.toml'
            self.authority.write_text(
                'model="fixture-global"\nmodel_provider="fixture"\n'
                'approval_policy="on-request"\napprovals_reviewer="user"\n'
                '[features]\nenable_request_compression=false\nremote_plugin=false\n'
                'recommended_plugins=false\napps=false\nrespect_system_proxy=false\n'
                '[analytics]\nenabled=false\n[model_providers.fixture]\nname="Fixture"\n'
                f'base_url={json.dumps(self.url)}\nwire_api="responses"\nrequires_openai_auth=false\n'
                f'supports_websockets=false\n[projects.{json.dumps(str(self.workspace))}]\ntrust_level="trusted"\n')
            (self.native / 'rules').mkdir()
            (self.native / 'rules/existing.rules').write_text('prefix_rule(pattern=["git", "status"], decision="allow")\n')
        else:
            self.env.update(COPILOT_HOME=str(self.native), COPILOT_CACHE_HOME=str(base / 'cache'),
                            COPILOT_OFFLINE='true', COPILOT_PROVIDER_TYPE='openai',
                            COPILOT_PROVIDER_WIRE_API='completions', COPILOT_PROVIDER_BASE_URL=self.url,
                            COPILOT_MODEL='gpt-5.4')
            self.authority = self.native / 'config.json'
            write_json(self.authority, {'firstLaunchAt': 1234})
            write_json(self.native / 'settings.json', {'model': 'gpt-5.4', 'trustedFolders': [str(self.workspace)]})
            write_json(self.native / 'permissions-config.json', {'locations': {str(self.workspace): {
                'tool_approvals': [{'kind': 'commands', 'commandIdentifiers': ['git status']}], 'allowed_directories': []}}})
        self.authority.chmod(0o600)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)

    def user_snapshot(self):
        return snapshot(self.native, USER_FILES)

    def configuration(self):
        paths = [self.native / name for name in USER_FILES]
        paths += [self.workspace / name for name in ('.codex/config.toml', '.github/copilot-instructions.md',
                                                    '.agents/permissions/development.json')]
        return {str(path.relative_to(self.base)): path.read_text() for path in paths if path.is_file()}

    def invoke(self, *arguments, expected=0):
        def authority():
            paths = [self.authority, self.native / 'settings.json', self.native / 'permissions-config.json',
                     self.native / 'rules/existing.rules', self.workspace / '.github/copilot/settings.json']
            return snapshot(self.base, [str(path.relative_to(self.base)) for path in paths])
        before = authority()
        user_before = self.user_snapshot()
        command = [str(self.cli), *map(str, arguments)]
        result = subprocess.run(command, cwd=self.workspace, env=self.env,
                                capture_output=True, text=True, timeout=60)
        row = {'command': command, 'exit_code': result.returncode, 'stdout': result.stdout,
               'stderr': result.stderr, 'expect_success': expected == 0,
               'authority_before': before, 'authority_after': authority(),
               'user_files_before': user_before, 'user_files_after': self.user_snapshot()}
        self.commands.append(row)
        if (result.returncode == 0) != (expected == 0) or row['authority_before'] != row['authority_after']:
            raise AssertionError(json.dumps(row))
        return row

    def apply(self, *extra, expected=0):
        arguments = ['apply', '--experimental', '--vendor', self.vendor, '--format', 'json']
        if self.vendor == 'codex':
            arguments += ['--preset', 'development']
        return self.invoke(*arguments, *extra, expected=expected)

    def doctor(self, phase, state):
        before = inspection_snapshot(self.base)
        row = self.invoke('doctor', '--experimental', '--vendor', self.vendor, '--format', 'json',
                          expected=0 if state in ('current', 'not-selected') else 1)
        row.update(doctor_phase=phase, inspection_before=before, inspection_after=inspection_snapshot(self.base))
        report = json.loads(row['stdout'])
        if (report.get('configuration_state') != state or row['inspection_before'] != row['inspection_after']
                or report.get('scope') != ('development-only' if self.vendor == 'codex' else 'full-project')
                or not any(check.get('id') == 'session.authority' and check.get('status') == 'unknown'
                           for check in report.get('checks', []))):
            raise AssertionError('doctor inspection did not preserve the fixture or report its expected state: ' + phase)
        return report

    def session(self, label, *, command=None, present=(), absent=(), host_read_only=False):
        self.command, self.nonce = command, 'AGENTS_DEVELOPMENT_' + uuid.uuid4().hex
        self.requests, self.provider_errors = [], []
        row = {'label': label, 'nonce': self.nonce, 'command': command, 'cwd': str(self.workspace),
               'expected_present': list(present), 'expected_absent': list(absent),
               'host_read_only': host_read_only}
        row['configuration'] = self.configuration()
        row['user_files_before'] = self.user_snapshot()
        self.sessions.append(row)
        arguments = ([str(self.binary), 'app-server', '--listen', 'stdio://'] if self.vendor == 'codex'
                     else [str(self.binary), '--acp', '--disable-builtin-mcps', '--no-auto-update', '--no-remote'])
        row['native_sha256'] = sha(self.binary)
        row['native_version'] = self.native_identity.get('native_version')
        if row['native_sha256'] != self.native_identity.get('native_sha256'):
            raise RuntimeError('native binary changed before session start')
        client = Client(arguments, self.workspace, self.env, 'deny', '')
        try:
            deadline = time.monotonic() + 45
            if self.vendor == 'codex':
                client.response(client.request('initialize', {'clientInfo': {'name': 'development-workflow', 'version': '1'},
                                'capabilities': {'experimentalApi': True}}), deadline)
                client.send({'method': 'initialized', 'params': {}})
                row['effective_config'] = client.response(client.request('config/read', {'cwd': str(self.workspace), 'includeLayers': True}), deadline)
                params = {'cwd': str(self.workspace), 'ephemeral': True}
                if host_read_only:
                    params['sandbox'] = 'read-only'
                row['thread'] = client.response(client.request('thread/start', params), deadline)
                if host_read_only:
                    row['diagnosis'] = {'requested_profile': 'agents-development',
                                        'host_override': 'read-only',
                                        'effective_sandbox': row['thread'].get('sandbox'),
                                        'message': 'The native session uses a read-only host override. Project allow decisions cannot remove it.'}
                identifier = row['thread']['thread']['id']
                row['session_id'] = identifier
                client.response(client.request('turn/start', {'threadId': identifier,
                    'input': [{'type': 'text', 'text': self.nonce + ': run the disposable fixture command once.'}]}), deadline)
                while True:
                    event = client.receive(deadline)
                    if event.get('method') == 'turn/completed' and event.get('params', {}).get('threadId') == identifier:
                        row['completion'] = event['params']
                        break
            else:
                client.response(client.request('initialize', {'protocolVersion': 1, 'clientCapabilities': {}}), deadline)
                session = client.response(client.request('session/new', {'cwd': str(self.workspace), 'mcpServers': []}), deadline)
                row['session_id'] = session['sessionId']
                row['completion'] = client.response(client.request('session/prompt', {'sessionId': session['sessionId'],
                    'prompt': [{'type': 'text', 'text': self.nonce + ': inspect the fixture instructions.'}]}), deadline)
        except Exception as error:
            row['error'] = str(error)
        finally:
            client.close()
            row.update(requests=self.requests, events=client.events, approvals=client.approvals,
                       stderr=client.errors, provider_errors=self.provider_errors,
                       process_exited=client.process.poll() is not None,
                       configuration_after=self.configuration(), user_files_after=self.user_snapshot())
        return row
