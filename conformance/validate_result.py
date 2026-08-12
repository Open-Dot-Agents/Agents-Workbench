#!/usr/bin/env python3
"""Validate one evidence result against the published result contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: validate_result.py RESULT SCHEMA", file=sys.stderr)
        return 2
    result = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    schema = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
