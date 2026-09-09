#!/usr/bin/env python3
"""Record native hook input and optional delay/decision in a disposable case."""
import json
import os
import sys
import time
from pathlib import Path

log, tag = Path(sys.argv[1]), sys.argv[2]
try:
    payload = json.load(sys.stdin)
    valid = isinstance(payload, dict)
except (ValueError, EOFError):
    payload, valid = {}, False
entry = {'tag': tag, 'phase': 'start', 'at': time.monotonic(), 'pid': os.getpid(), 'jsonObject': valid}
for key in ('hook_event_name', 'eventName', 'tool_name', 'toolName', 'source', 'reason', 'agent_type', 'agentName', 'agentType', 'trigger'):
    if key in payload: entry[key] = payload[key]
with log.open('a') as output: output.write(json.dumps(entry)+'\n')
if len(sys.argv) > 3: time.sleep(float(sys.argv[3]))
with log.open('a') as output: output.write(json.dumps({'tag':tag,'phase':'end','at':time.monotonic()})+'\n')
if len(sys.argv) > 4:
    mode = sys.argv[4]
    if mode == 'exit2':
        print('ODA controlled hook rejection', file=sys.stderr)
        raise SystemExit(2)
    if mode == 'deny-codex':
        print(json.dumps({'hookSpecificOutput':{'hookEventName':'PreToolUse','permissionDecision':'deny','permissionDecisionReason':'ODA controlled refusal'}}))
    elif mode == 'deny-copilot':
        print(json.dumps({'permissionDecision':'deny','permissionDecisionReason':'ODA controlled refusal'}))
    elif mode == 'permission-codex':
        print(json.dumps({'hookSpecificOutput':{'hookEventName':'PermissionRequest','decision':{'behavior':'allow'}}}))
    elif mode == 'permission-copilot':
        print(json.dumps({'behavior':'allow'}))
