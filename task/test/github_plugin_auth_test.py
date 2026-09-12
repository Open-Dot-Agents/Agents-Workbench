"""Check probe expectations for the bundled and original GitHub packages."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'WORKBENCH/conformance'))
from run_github_plugin_auth import expected_authentication


class GitHubPluginAuthTests(unittest.TestCase):
    def test_bundled_header_works_without_overlay_flags(self):
        package = ROOT / '.agents/plugins/com.openai.codex/plugins/github/.mcp.json'
        server = json.loads(package.read_text())['mcpServers']['github']
        for vendor in ('codex', 'copilot'):
            with self.subTest(vendor=vendor):
                self.assertTrue(expected_authentication(vendor, True, server))
                self.assertFalse(expected_authentication(vendor, False, server))

    def test_original_bearer_field_retains_copilot_limitation(self):
        server = {'bearer_token_env_var': 'GITHUB_PAT_TOKEN'}
        self.assertTrue(expected_authentication('codex', True, server))
        self.assertFalse(expected_authentication('copilot', True, server))
        self.assertFalse(expected_authentication('codex', False, server))

    def test_missing_or_unrelated_fields_do_not_establish_authentication(self):
        for server in ({}, {'headers': {'X-Test': 'Bearer ${GITHUB_PAT_TOKEN}'}},
                       {'bearer_token_env_var': 'OTHER_TOKEN'}):
            for vendor in ('codex', 'copilot'):
                with self.subTest(server=server, vendor=vendor):
                    self.assertFalse(expected_authentication(vendor, True, server))
