"""Check that MCP calls cannot satisfy hook execution checks."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class MarkerServerTests(unittest.TestCase):
    def test_hook_marker_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "markers.jsonl"
            requests = [{"jsonrpc": "2.0", "id": index, "method": "tools/call",
                         "params": {"name": "record", "arguments": {"marker": marker}}}
                        for index, marker in enumerate(["native-hook", "root-instruction"])]
            result = subprocess.run(["python3", str(ROOT / "conformance/marker_server.py"), str(log)],
                                    input="".join(json.dumps(r) + "\n" for r in requests),
                                    text=True, capture_output=True, check=True)
            replies = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertTrue(replies[0]["result"]["isError"])
            self.assertEqual([json.loads(line)["marker"] for line in log.read_text().splitlines()],
                             ["root-instruction"])
