"""Test historical evidence integrity and current support eligibility."""

import hashlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "conformance"))
from evidence_state import assess_receipt, summarize_receipts


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class EvidenceStateTests(unittest.TestCase):
    def fixture(self, directory: Path):
        repository = directory / "repository"
        repository.mkdir()
        implementation = repository / "implementation.go"
        implementation.write_bytes(b"current implementation\n")
        runner = directory / "current_runner.py"
        runner.write_bytes(b"current runner\n")
        receipt = directory / "result.json"
        snapshot = receipt.with_suffix(".runner.py")
        snapshot.write_bytes(runner.read_bytes())
        record = {
            "passed": True,
            "native_sha256": "a" * 64,
            "runner_sha256": digest(snapshot.read_bytes()),
            "implementation_sha256": {"implementation.go": digest(implementation.read_bytes())},
        }
        receipt.write_text(json.dumps(record), encoding="utf-8")
        return repository, runner, receipt, record

    def test_intact_historical_receipt_passes_integrity(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository, runner, receipt, _ = self.fixture(Path(temporary))
            state = assess_receipt(receipt, repository, current_runner=runner)
            self.assertTrue(state.integrity_valid, state.integrity_errors)
            self.assertTrue(state.current_eligible, state.eligibility_errors)

    def test_altered_receipt_fails_integrity(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository, runner, receipt, record = self.fixture(Path(temporary))
            record["runner_sha256"] = "b" * 64
            receipt.write_text(json.dumps(record), encoding="utf-8")
            state = assess_receipt(receipt, repository, current_runner=runner)
            self.assertFalse(state.integrity_valid)
            self.assertIn("captured runner hash differs from receipt", state.integrity_errors)

    def test_altered_captured_runner_fails_integrity(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository, runner, receipt, _ = self.fixture(Path(temporary))
            receipt.with_suffix(".runner.py").write_bytes(b"altered\n")
            state = assess_receipt(receipt, repository, current_runner=runner)
            self.assertFalse(state.integrity_valid)

    def test_historical_runner_drift_only_removes_current_eligibility(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository, runner, receipt, _ = self.fixture(Path(temporary))
            runner.write_bytes(b"new runner\n")
            state = assess_receipt(receipt, repository, current_runner=runner)
            self.assertTrue(state.integrity_valid, state.integrity_errors)
            self.assertFalse(state.current_eligible)
            self.assertIn("current runner differs from captured runner", state.eligibility_errors)

    def test_implementation_drift_only_removes_current_eligibility(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository, runner, receipt, _ = self.fixture(Path(temporary))
            (repository / "implementation.go").write_bytes(b"new implementation\n")
            state = assess_receipt(receipt, repository, current_runner=runner)
            self.assertTrue(state.integrity_valid, state.integrity_errors)
            self.assertFalse(state.current_eligible)
            self.assertIn("current implementation source differs: implementation.go", state.eligibility_errors)

    def test_captured_source_or_fixture_drift_fails_integrity(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            repository, runner, receipt, record = self.fixture(directory)
            source = directory / "fixture.json"
            source.write_bytes(b"captured fixture\n")
            record["fixture_sha256"] = {source.name: digest(source.read_bytes())}
            receipt.write_text(json.dumps(record), encoding="utf-8")
            self.assertTrue(assess_receipt(receipt, repository, current_runner=runner).integrity_valid)
            source.write_bytes(b"altered fixture\n")
            state = assess_receipt(receipt, repository, current_runner=runner)
            self.assertFalse(state.integrity_valid)
            self.assertIn("captured artifact differs: fixture.json", state.integrity_errors)

    def test_historical_verifiers_do_not_require_current_source_hashes(self):
        prohibited = (
            re.compile(r"assert .*runner_sha256.*sha\((?:ROOT|__file__)"),
            re.compile(r"assert .*helper_sha256.*sha\(ROOT"),
            re.compile(r"assert .*implementation_sha256.*==.*(?:files|implementation)"),
        )
        failures = []
        for path in sorted((ROOT / "conformance").glob("verify*.py")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if any(pattern.search(line) for pattern in prohibited):
                    failures.append(f"{path.name}:{number}: {line.strip()}")
        self.assertEqual(failures, [])

    def test_receipt_set_summary_keeps_integrity_and_eligibility_separate(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository, runner, receipt, _ = self.fixture(Path(temporary))
            runner.write_bytes(b"new runner\n")
            summary = summarize_receipts([receipt], repository, current_runner=runner)
            self.assertTrue(summary["historical_integrity"])
            self.assertFalse(summary["current_support_eligible"])
            self.assertEqual(summary["integrity_errors"], {})
            self.assertIn(str(receipt), summary["current_support_reasons"])

    def test_current_runner_comparison_is_required_for_eligibility(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository, _, receipt, _ = self.fixture(Path(temporary))
            state = assess_receipt(receipt, repository)
            self.assertTrue(state.integrity_valid)
            self.assertFalse(state.current_eligible)
            self.assertIn("current runner comparison is missing", state.eligibility_errors)

    def test_empty_implementation_map_is_not_current_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            repository, runner, receipt, record = self.fixture(Path(temporary))
            record["implementation_sha256"] = {}
            receipt.write_text(json.dumps(record), encoding="utf-8")
            state = assess_receipt(receipt, repository, current_runner=runner)
            self.assertTrue(state.integrity_valid)
            self.assertFalse(state.current_eligible)
            self.assertIn("implementation_sha256 has no source entries", state.eligibility_errors)

    def test_each_declared_helper_requires_a_current_comparison(self):
        for helper_form in ("scalar", "map"):
            with self.subTest(helper_form=helper_form), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                repository, runner, receipt, record = self.fixture(directory)
                helper = directory / "helper.py"
                helper.write_bytes(b"helper source\n")
                helper_digest = digest(helper.read_bytes())
                record["helper_sha256"] = helper_digest if helper_form == "scalar" else {helper.name: helper_digest}
                receipt.write_text(json.dumps(record), encoding="utf-8")
                state = assess_receipt(receipt, repository, current_runner=runner)
                self.assertTrue(state.integrity_valid)
                self.assertFalse(state.current_eligible)
                kwargs = {"current_helper": helper} if helper_form == "scalar" else {"current_helpers": {helper.name: helper}}
                self.assertTrue(assess_receipt(receipt, repository, current_runner=runner, **kwargs).current_eligible)
                helper.write_bytes(b"changed helper\n")
                state = assess_receipt(receipt, repository, current_runner=runner, **kwargs)
                self.assertTrue(state.integrity_valid)
                self.assertFalse(state.current_eligible)

    def test_partial_helper_comparison_is_not_current_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            repository, runner, receipt, record = self.fixture(directory)
            helper = directory / "helper.py"
            helper.write_bytes(b"helper source\n")
            record["helper_sha256"] = {helper.name: digest(helper.read_bytes()), "other.py": "c" * 64}
            receipt.write_text(json.dumps(record), encoding="utf-8")
            state = assess_receipt(receipt, repository, current_runner=runner, current_helpers={helper.name: helper})
            self.assertTrue(state.integrity_valid)
            self.assertFalse(state.current_eligible)
            self.assertIn("current helper comparison is missing: other.py", state.eligibility_errors)

    def test_empty_receipt_set_cannot_establish_integrity_or_eligibility(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            summary = summarize_receipts([], directory, current_runner=directory / "runner.py")
            self.assertFalse(summary["historical_integrity"])
            self.assertFalse(summary["current_support_eligible"])
            self.assertEqual(summary["receipt_count"], 0)

    def test_receipt_verifiers_report_current_eligibility(self):
        missing = []
        for path in sorted((ROOT / "conformance").glob("verify*.py")):
            source = path.read_text(encoding="utf-8")
            if "runner_sha256" not in source:
                continue
            if "summarize_receipts" not in source and "current_support_eligib" not in source and "current eligibility" not in source:
                missing.append(path.name)
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
