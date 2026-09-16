"""Cross-check the draft schemas and the public CLI on the same fixtures."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[3]


class SecurityDraftTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='oda-security-test-')
        cls.work = Path(cls.temporary.name)
        cls.agents = cls.work / 'agents'
        subprocess.run(['go', 'build', '-buildvcs=false', '-o', str(cls.agents), './cmd/agents'], cwd=ROOT / 'CLI', check=True)
        module_path = ROOT / 'SPEC/conformance/security_draft.py'
        spec = importlib.util.spec_from_file_location('security_draft', module_path)
        cls.fixture_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.fixture_module)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_schema_and_cli_agree(self):
        cases = json.loads((ROOT / 'SPEC/conformance/security_cases.json').read_text())
        for case in cases:
            with self.subTest(case=case['id']):
                fixture = self.work / case['id']
                shutil.copytree(ROOT / 'SPEC/examples' / case.get('example', 'security-draft'), fixture)
                path = fixture / '.agents' / self.fixture_module.PATHS[case['document']]
                path.write_text(self.fixture_module.document(case))
                result = subprocess.run([str(self.agents), 'validate', '--experimental', '--root', str(fixture), '--format', 'json'], capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, case['valid'], result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload['passed'], case['valid'])
                directory = {
                    '1.0.0': '1.0',
                    '1.1.0-draft.1': '1.1-draft',
                    '1.1.0-draft.2': '1.1-draft.2',
                }[payload['schemaVersion']]
                schema = json.loads((ROOT / 'SPEC/spec' / directory / 'schemas/conformance-result.schema.json').read_text())
                Draft202012Validator(schema).validate(payload)

    def test_native_versions_require_exact_pins(self):
        path = ROOT / 'WORKBENCH/conformance/run_security.py'
        spec = importlib.util.spec_from_file_location('security_runner', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(module.version_matches('GitHub Copilot CLI 1.0.84-9.', '1.0.84-9'))
        self.assertFalse(module.version_matches('GitHub Copilot CLI 1.0.84-90.', '1.0.84-9'))
        self.assertFalse(module.version_matches('codex-cli 0.154.1', '0.154.0'))

    def test_required_and_optional_extensions_remain_inactive(self):
        fixture = self.work / 'extensions'
        shutil.copytree(ROOT / 'SPEC/examples/security-draft', fixture)
        path = fixture / '.agents/permissions/permissions.json'
        policy = json.loads(path.read_text())
        for required, code in [(True, 'ODA-SECURITY-0003'), (False, 'ODA-SECURITY-0005')]:
            policy['extensions'] = {'com.example.policy': {'required': required, 'data': {'option': 'value'}}}
            path.write_text(json.dumps(policy))
            for vendor in ['codex', 'copilot']:
                result = subprocess.run([str(self.agents), 'plan', '--experimental', '--root', str(fixture), '--vendor', vendor, '--format', 'json'], capture_output=True, text=True, check=True)
                payload = json.loads(result.stdout)
                self.assertFalse(payload['applicable'])
                self.assertIn(code, ' '.join(payload['diagnostics']))
                self.assertEqual(payload['security']['normalized']['permissions']['extensions'], policy['extensions'])


if __name__ == '__main__':
    unittest.main()
