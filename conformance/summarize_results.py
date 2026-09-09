#!/usr/bin/env python3
"""Summarize native adapter evidence records."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


VENDORS = ("copilot", "codex", "claude")
LEGACY_CREDENTIAL_CHECKS = {
    "preflight.gh.token": "preflight.copilot.credential",
    "preflight.openai.api.key": "preflight.codex.credential",
    "preflight.anthropic.api.key": "preflight.claude.credential",
}


def failed_checks(result: dict[str, object]) -> list[str]:
    checks = result.get("checks")
    if not isinstance(checks, list):
        return ["checks"]
    failed: list[str] = []
    for check in checks:
        if not isinstance(check, dict):
            failed.append("malformed-check")
            continue
        check_id = check.get("id")
        if check.get("passed") is not True:
            failed.append(str(check_id) if check_id else "unknown-check")
    normalized = set(failed)
    for legacy, replacement in LEGACY_CREDENTIAL_CHECKS.items():
        if legacy in normalized and replacement in normalized:
            normalized.remove(legacy)
    failed = [check for check in failed if check in normalized]
    return failed


def summarize(result_dir: Path, suffix: str, vendors: tuple[str, ...] = VENDORS) -> tuple[bool, list[str]]:
    lines: list[str] = []
    ok = True
    for vendor in vendors:
        path = result_dir / f"{vendor}{suffix}.json"
        if not path.exists():
            ok = False
            lines.append(f"{vendor}: missing {path}")
            continue
        result = json.loads(path.read_text(encoding="utf-8"))
        if result.get("passed") is True:
            lines.append(f"{vendor}: passed")
            continue
        ok = False
        failed = failed_checks(result)
        if failed:
            lines.append(f"{vendor}: failed ({', '.join(failed)})")
        else:
            lines.append(f"{vendor}: failed")
    return ok, lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-dir", type=Path, default=Path("evidence/results"))
    parser.add_argument("--suffix", choices=("", "-preflight"), default="")
    parser.add_argument("--vendors", nargs="+", choices=VENDORS, default=list(VENDORS))
    args = parser.parse_args()

    ok, lines = summarize(args.result_dir, args.suffix, tuple(args.vendors))
    for line in lines:
        print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
