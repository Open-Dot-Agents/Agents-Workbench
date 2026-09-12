#!/usr/bin/env python3
"""Separate retained evidence integrity from current support eligibility."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


SHA256 = re.compile(r"[0-9a-f]{64}")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class EvidenceState:
    integrity_errors: tuple[str, ...]
    eligibility_errors: tuple[str, ...]

    @property
    def integrity_valid(self) -> bool:
        return not self.integrity_errors

    @property
    def current_eligible(self) -> bool:
        return self.integrity_valid and not self.eligibility_errors


def _hash_map(value: object, field: str, errors: list[str]) -> dict[str, str]:
    if not isinstance(value, dict):
        errors.append(f"{field} must be an object")
        return {}
    result: dict[str, str] = {}
    for name, digest in value.items():
        if not isinstance(name, str) or not name or not isinstance(digest, str) or not SHA256.fullmatch(digest):
            errors.append(f"{field} must contain path to SHA-256 entries")
            continue
        result[name] = digest
    return result


def assess_receipt(
    receipt_path: Path,
    repository_root: Path,
    *,
    current_runner: Path | None = None,
    current_helper: Path | None = None,
    current_helpers: Mapping[str, Path] | None = None,
) -> EvidenceState:
    """Assess a captured native receipt without rewriting historical claims.

    Integrity uses files captured with the receipt. Eligibility additionally
    compares the receipt with the current tracked runner and implementation.
    Missing comparisons cannot establish current eligibility. Each declared
    helper needs an explicit current path, and the source map must not be empty.
    """
    integrity: list[str] = []
    eligibility: list[str] = []
    if current_runner is None:
        eligibility.append("current runner comparison is missing")
    try:
        record = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return EvidenceState((f"receipt cannot be read: {error}",), ())
    if not isinstance(record, dict):
        return EvidenceState(("receipt must be an object",), ())

    runner_digest = record.get("runner_sha256")
    if not isinstance(runner_digest, str) or not SHA256.fullmatch(runner_digest):
        integrity.append("runner_sha256 must be a SHA-256 digest")
    else:
        snapshot = receipt_path.with_suffix(".runner.py")
        if not snapshot.is_file():
            integrity.append("captured runner is missing")
        elif sha256(snapshot) != runner_digest:
            integrity.append("captured runner hash differs from receipt")
        if current_runner is not None:
            if not current_runner.is_file():
                eligibility.append("current runner is missing")
            elif sha256(current_runner) != runner_digest:
                eligibility.append("current runner differs from captured runner")

    native_digest = record.get("native_sha256")
    if native_digest is not None and (not isinstance(native_digest, str) or not SHA256.fullmatch(native_digest)):
        integrity.append("native_sha256 must be a SHA-256 digest")

    if "implementation_sha256" in record:
        implementation = _hash_map(record["implementation_sha256"], "implementation_sha256", integrity)
        if not implementation:
            eligibility.append("implementation_sha256 has no source entries")
    else:
        implementation = {}
        eligibility.append("implementation_sha256 is missing")
    for name, digest in implementation.items():
        source = repository_root / name
        if not source.is_file():
            eligibility.append(f"current implementation source is missing: {name}")
        elif sha256(source) != digest:
            eligibility.append(f"current implementation source differs: {name}")

    helpers = record.get("helper_sha256")
    if isinstance(helpers, dict):
        helper_hashes = _hash_map(helpers, "helper_sha256", integrity)
        for name, digest in helper_hashes.items():
            current = (current_helpers or {}).get(name)
            if current is None:
                eligibility.append(f"current helper comparison is missing: {name}")
            elif not current.is_file() or sha256(current) != digest:
                eligibility.append(f"current helper differs: {name}")
    elif isinstance(helpers, str) and SHA256.fullmatch(helpers):
        if current_helper is None:
            eligibility.append("current helper comparison is missing")
        elif not current_helper.is_file() or sha256(current_helper) != helpers:
            eligibility.append(f"current helper differs: {current_helper.name}")
    elif helpers is not None:
        integrity.append("helper_sha256 must be a SHA-256 digest or object")

    for field in ("artifact_sha256", "source_artifact_sha256", "fixture_sha256"):
        if field not in record:
            continue
        for name, digest in _hash_map(record[field], field, integrity).items():
            artifact = receipt_path.parent / name
            if not artifact.is_file() or sha256(artifact) != digest:
                integrity.append(f"captured artifact differs: {name}")

    return EvidenceState(tuple(integrity), tuple(eligibility))


def summarize_receipts(
    receipt_paths: list[Path],
    repository_root: Path,
    *,
    current_runner: Path,
    current_helper: Path | None = None,
    current_helpers: Mapping[str, Path] | None = None,
) -> dict[str, object]:
    """Return one structured integrity and eligibility result for receipts."""
    states = {
        str(path): assess_receipt(
            path,
            repository_root,
            current_runner=current_runner,
            current_helper=current_helper,
            current_helpers=current_helpers,
        )
        for path in receipt_paths
    }
    return {
        "receipt_count": len(states),
        "historical_integrity": bool(states) and all(state.integrity_valid for state in states.values()),
        "current_support_eligible": bool(states) and all(state.current_eligible for state in states.values()),
        "integrity_errors": {
            path: list(state.integrity_errors)
            for path, state in states.items()
            if state.integrity_errors
        },
        "current_support_reasons": {
            path: list(state.eligibility_errors)
            for path, state in states.items()
            if state.eligibility_errors
        },
    }
