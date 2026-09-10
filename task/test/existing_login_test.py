"""Test local login selection without reading or copying stored credentials."""
import json
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'conformance'))
import run_adapter


class ExistingLoginTests(unittest.TestCase):
    def test_auto_uses_existing_copilot_login_without_claiming_authentication(self):
        with mock.patch.dict(os.environ, {'AGENTS_BIN': '/fake/agents'}, clear=True), \
             mock.patch.object(run_adapter, 'harness_executable', return_value=('/fake/copilot', 'COPILOT_BIN')), \
             mock.patch.object(run_adapter.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'GitHub Copilot CLI 1.0.83.')):
            checks, metadata = run_adapter.preflight('copilot', 'auto')
        self.assertTrue(all(c['passed'] for c in checks))
        self.assertEqual(metadata['authMode'], 'existing-login')
        self.assertEqual(metadata['authenticationVerification'], 'native-request-required')
        self.assertNotIn('credentialEnv', metadata)

    def test_codex_existing_login_requires_status_success(self):
        for returncode in (0, 1):
            with mock.patch.dict(os.environ, {'AGENTS_BIN': '/fake/agents'}, clear=True), \
                 mock.patch.object(run_adapter, 'harness_executable', return_value=('/fake/codex', 'CODEX_BIN')), \
                 mock.patch.object(run_adapter.subprocess, 'run', side_effect=[
                     subprocess.CompletedProcess([], 0, 'codex-cli 0.154.0'),
                     subprocess.CompletedProcess([], returncode, 'status')]):
                checks, _ = run_adapter.preflight('codex', 'auto')
            self.assertEqual(all(c['passed'] for c in checks), returncode == 0)

    def test_environment_mode_still_requires_credentials(self):
        with mock.patch.dict(os.environ, {'AGENTS_BIN': '/fake/agents'}, clear=True), \
             mock.patch.object(run_adapter, 'harness_executable', return_value=('/fake/copilot', 'COPILOT_BIN')), \
             mock.patch.object(run_adapter.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'GitHub Copilot CLI 1.0.83.')):
            checks, metadata = run_adapter.preflight('copilot', 'environment')
        self.assertFalse(all(c['passed'] for c in checks))
        self.assertEqual(metadata['authMode'], 'environment')

    def test_existing_login_copies_auth_to_private_temporary_state(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source'
            source.mkdir()
            (source / 'auth.json').write_text('{"test":"fake-auth"}')
            with mock.patch.dict(os.environ, {'CODEX_HOME': str(source)}, clear=True), \
                 mock.patch.object(run_adapter.subprocess, 'run') as run:
                metadata = {'authMode': 'existing-login'}
                environment = run_adapter.harness_environment('codex', '/fake/codex', metadata, Path(directory))
            copied = Path(environment['CODEX_HOME']) / 'auth.json'
            self.assertEqual(copied.read_bytes(), (source / 'auth.json').read_bytes())
            self.assertEqual(copied.stat().st_mode & 0o777, 0o600)
            self.assertEqual(copied.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(metadata['runtimeHome'], 'temporary')
            run.assert_not_called()

    def test_copilot_jsonc_and_trust_are_confined_to_temporary_config(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source'
            source.mkdir()
            original = '// comment\n{"token":"https://fake.test/a//b", "hooks":{},}\n'
            (source / 'config.json').write_text(original)
            with mock.patch.dict(os.environ, {'COPILOT_HOME': str(source)}, clear=True):
                metadata = {'authMode': 'existing-login'}
                environment = run_adapter.harness_environment('copilot', '/fake/copilot', metadata, Path(directory))
            config = json.loads((Path(environment['COPILOT_HOME']) / 'config.json').read_text())
            self.assertEqual(config['token'], 'https://fake.test/a//b')
            self.assertEqual(config['trustedFolders'], [str(Path(directory)/'repository'), str(Path(directory)/'disabled-repository')])
            self.assertNotIn('hooks', config)
            self.assertEqual((source/'config.json').read_text(), original)

    def test_claude_uses_empty_private_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'personal'
            source.mkdir()
            (source/'settings.json').write_text('{"hooks":{"SessionStart":[]}}')
            with mock.patch.dict(os.environ, {'CLAUDE_CONFIG_DIR':str(source), 'ANTHROPIC_API_KEY':'synthetic-test'}, clear=True):
                metadata = {'authMode':'environment'}
                environment = run_adapter.harness_environment('claude','/fake/claude',metadata,Path(directory))
            runtime = Path(environment['CLAUDE_CONFIG_DIR'])
            self.assertNotEqual(runtime,source)
            self.assertEqual(list(runtime.iterdir()),[])
            self.assertEqual(runtime.stat().st_mode & 0o777,0o700)
            self.assertTrue((source/'settings.json').exists())
            self.assertEqual(environment['ANTHROPIC_API_KEY'],'synthetic-test')

    def test_native_mcp_parser_rejects_shell_and_failed_tool_calls(self):
        start = {'type':'tool.execution_start','data':{'toolCallId':'1','toolName':'oda-marker-record','arguments':{'marker':'root-instruction'}}}
        done = {'type':'tool.execution_complete','data':{'toolCallId':'1','success':True}}
        output = lambda: '\n'.join(map(json.dumps, [start,done]))
        self.assertEqual(run_adapter.native_mcp_markers('copilot',output()), ['root-instruction'])
        done['data']['success'] = False
        self.assertEqual(run_adapter.native_mcp_markers('copilot',output()), [])
        done['data']['success'] = True
        start['data']['toolName'] = 'bash'
        self.assertEqual(run_adapter.native_mcp_markers('copilot',output()), [])
        event = {'type':'item.completed','item':{'type':'mcp_tool_call','server':'oda-marker','tool':'record','status':'completed','arguments':{'marker':'root-instruction'},'result':{}}}
        self.assertEqual(run_adapter.native_mcp_markers('codex',json.dumps(event)), ['root-instruction'])
        event['item']['result']['isError'] = True
        self.assertEqual(run_adapter.native_mcp_markers('codex',json.dumps(event)), [])
