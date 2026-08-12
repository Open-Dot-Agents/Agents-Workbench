#!/usr/bin/env python3
"""Minimal stdio MCP server used only by isolated adapter conformance runs."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def respond(identifier: object, result: object) -> None:
    print(json.dumps({"jsonrpc": "2.0", "id": identifier, "result": result}), flush=True)


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    marker_path = Path(sys.argv[1])
    for line in sys.stdin:
        request = json.loads(line)
        method = request.get("method")
        identifier = request.get("id")
        if identifier is None:
            continue
        if method == "initialize":
            respond(identifier, {
                "protocolVersion": request.get("params", {}).get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "oda-marker", "version": "1.0.0"},
            })
        elif method == "tools/list":
            respond(identifier, {"tools": [{
                "name": "record",
                "description": "Record one Open-Dot-Agents conformance marker.",
                "inputSchema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["marker"],
                    "properties": {"marker": {"type": "string"}},
                },
            }]})
        elif method == "tools/call":
            arguments = request.get("params", {}).get("arguments", {})
            marker_path.parent.mkdir(parents=True, exist_ok=True)
            with marker_path.open("a", encoding="utf-8") as output:
                output.write(json.dumps({"marker": arguments.get("marker")}) + "\n")
            respond(identifier, {"content": [{"type": "text", "text": "recorded"}]})
        else:
            respond(identifier, {})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
