"""Synthetic verifier inputs only. These are not native execution evidence."""


def instruction_phase(markers, label='source'):
    """One completed fixture read with correlated session and tool events."""
    workspace = '/synthetic-workspace'
    session = 'synthetic-' + label
    return {
        'label': label, 'workspace': workspace, 'nonce': 'synthetic-nonce',
        'prompt': {'stopReason': 'end_turn'}, 'approvals': [],
        'session': {'sessionId': session},
        'requests': [
            {'messages': [{'role': 'system', 'content': ' '.join(markers)},
                          {'role': 'user', 'content': 'synthetic-nonce'}]},
            {'messages': [{'role': 'tool', 'content': 'AGENTS_FILE_READ'}]},
        ],
        'native_events': [
            {'type': 'session.start', 'data': {'sessionId': session,
                'copilotVersion': '1.0.83', 'context': {'cwd': workspace}}},
            {'type': 'user.message', 'data': {'content': 'synthetic-nonce'}},
            {'type': 'tool.execution_start', 'data': {'toolName': 'view',
                'toolCallId': 'synthetic-read', 'arguments': {'path': workspace+'/fixture.txt'}}},
            {'type': 'tool.execution_complete', 'data': {'toolCallId': 'synthetic-read',
                'success': True, 'result': {'content': 'AGENTS_FILE_READ'}}},
            {'type': 'assistant.turn_end', 'data': {}},
        ],
        'events': [{'method': 'session/update', 'params': {'sessionId': session}}],
    }
