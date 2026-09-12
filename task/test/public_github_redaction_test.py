"""Do not persist a credential reflected by an external response or error."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'conformance'))
from probe_public_github_mcp import redact_credential_result


class PublicGitHubRedactionTests(unittest.TestCase):
    def test_reflected_response_is_removed_even_after_failure(self):
        token = 'synthetic-fixture-credential'
        record = {'passed': False, 'requests': [{'status': 400, 'body': 'Bearer ' + token}]}
        result = redact_credential_result(record, token)
        self.assertFalse(result['passed'])
        self.assertNotIn(token, json.dumps(result))
        self.assertNotIn('requests', result)

    def test_error_message_cannot_persist_a_credential(self):
        token = 'synthetic-fixture-credential'
        result = redact_credential_result({'passed': False, 'error': token}, token)
        self.assertNotIn(token, json.dumps(result))
        self.assertFalse(result['credential_value_stored'])

    def test_clean_record_is_preserved(self):
        record = {'passed': True, 'requests': [{'status': 200, 'body': 'fixture'}]}
        self.assertEqual(redact_credential_result(record, 'synthetic-fixture-credential'), record)
