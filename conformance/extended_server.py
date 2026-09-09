#!/usr/bin/env python3
"""Small MCP peer for native transport and environment tests."""
from __future__ import annotations
import json
import os
import sys
import time
from pathlib import Path


def record(path: Path, event: dict) -> None:
    event = {**event, 'at': time.monotonic()}
    with path.open('a') as output:
        output.write(json.dumps(event) + '\n')


def respond(request: dict, log: Path, expected_env: str | None = None) -> dict | None:
    identifier = request.get('id')
    if identifier is None:
        return None
    method = request.get('method')
    result: dict = {}
    if method == 'initialize':
        result = {'protocolVersion': request.get('params', {}).get('protocolVersion', '2025-06-18'),
                  'capabilities': {'tools': {}}, 'serverInfo': {'name': 'oda-marker', 'version': '1.0.0'}}
    elif method == 'tools/list':
        result = {'tools': [{'name': 'record', 'description': 'Record the marker specified by the applicable test instructions.',
                            'inputSchema': {'type': 'object', 'properties': {'marker': {'type': 'string'}},
                                            'required': ['marker'], 'additionalProperties': False}}]}
    elif method == 'tools/call':
        params = request.get('params', {})
        marker = params.get('arguments', {}).get('marker')
        if params.get('name') != 'record' or not isinstance(marker, str):
            result = {'isError': True, 'content': [{'type': 'text', 'text': 'invalid marker'}]}
        else:
            event = {'event': 'tool-call', 'marker': marker}
            if expected_env is not None:
                event['environmentMatches'] = os.environ.get('ODA_NATIVE_ENV') == expected_env
            record(log, event)
            result = {'content': [{'type': 'text', 'text': 'recorded'}]}
    return {'jsonrpc': '2.0', 'id': identifier, 'result': result}


def main() -> int:
    log = Path(sys.argv[1])
    record(log, {'event': 'startup', 'argv': sys.argv[3:]})
    expected_env = sys.argv[2] or None
    for line in sys.stdin:
        response = respond(json.loads(line), log, expected_env)
        if response is not None:
            print(json.dumps(response), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
