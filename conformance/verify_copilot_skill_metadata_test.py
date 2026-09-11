#!/usr/bin/env python3
"""Reject incomplete or contradictory native skill evidence."""
import copy
import hashlib
import json
import unittest

from verify_copilot_skill_metadata import BASE, evaluate, filename


class SkillEvidenceTest(unittest.TestCase):
    def test_retained_refusal_regression(self):
        path = BASE / 'copilot-skill-activation-refusal-before.json'
        r = json.loads(path.read_text())
        self.assertNotEqual(r['exit_code'], 0)
        self.assertEqual(r['stdout'].count('ignored native skill was offered as applicable:'), 6)
        self.assertEqual(r['test_sha256'], hashlib.sha256(path.with_suffix('.test.go').read_bytes()).hexdigest())

    def record(self, skill='allowed', trigger='model', interface='acp', decision='deny'):
        case = ('project', 'controls', skill, trigger, interface, decision)
        return json.loads((BASE / filename(case)).read_text())

    def rejects(self, record):
        with self.assertRaises((AssertionError, KeyError)):
            evaluate(record)

    def test_real_allow_and_deny_controls(self):
        for interface, decision in [('acp', 'deny'), ('cli', 'deny'), ('tui', 'deny'), ('tui', 'allow')]:
            r = self.record(trigger='user' if interface == 'tui' else 'model', interface=interface, decision=decision)
            self.assertTrue(evaluate(r)['permission_observation_verified'])

    def test_pass_flag_does_not_replace_native_events(self):
        r = self.record()
        r['native_events'] = []
        self.rejects(r)

    def test_denial_requires_approval_request(self):
        r = self.record()
        r['approvals'] = []
        self.rejects(r)

    def test_denial_requires_matching_tool_id(self):
        r = self.record()
        r['approvals'][0]['params']['toolCall']['toolCallId'] = 'unrelated'
        self.rejects(r)

    def test_denial_requires_matching_session(self):
        r = self.record()
        r['approvals'][0]['params']['sessionId'] = 'unrelated'
        self.rejects(r)

    def test_denied_command_cannot_have_effect(self):
        r = self.record()
        r['effect'] = 'AGENTS_SKILL_EFFECT'
        self.rejects(r)

    def test_allowed_command_requires_effect(self):
        r = self.record(trigger='user', interface='tui', decision='allow')
        r['effect'] = None
        self.rejects(r)

    def test_effect_cannot_precede_approval(self):
        r = self.record(trigger='user', interface='tui', decision='allow')
        r['terminal']['effect_before_decision'] = True
        self.rejects(r)

    def test_hidden_body_cannot_be_loaded(self):
        r = self.record(skill='model-hidden')
        r['requests'][-1]['messages'].append({'role': 'user', 'content': 'AGENTS_SKILL_BODY_model-hidden'})
        self.rejects(r)

    def test_skill_event_requires_matching_grant(self):
        r = self.record()
        for e in r['native_events']:
            if e['type'] == 'skill.invoked': e['data']['allowedTools'] = []
        self.rejects(r)

    def test_apply_cannot_change_skill_bytes(self):
        r = self.record()
        r['target_hashes']['allowed'] = 'changed'
        self.rejects(r)

    def test_refusal_requires_unchanged_files(self):
        case = ('project', 'malformed', None, 'model', 'acp', 'deny')
        r = json.loads((BASE / filename(case)).read_text())
        r['refusal_unchanged'] = False
        self.rejects(r)

    def test_apply_must_print_known_loss(self):
        r = self.record()
        for command in r['commands']:
            if len(command['command']) > 1 and command['command'][1] == 'apply':
                command['stdout'] = ''
        self.rejects(r)

    def test_catalog_can_be_in_tool_description(self):
        original = self.record()
        for destination in ('messages', 'tools'):
            r = copy.deepcopy(original)
            # Move only the known fixture description tokens. Preserve native
            # events and bodies so this tests the model-input location check.
            for request in r['requests']:
                for key in ('messages', 'tools'):
                    request[key] = json.loads(json.dumps(request[key]).replace('AGENTS_SKILL_DESCRIPTION_', 'REMOVED_DESCRIPTION_'))
            descriptions = '\n'.join('AGENTS_SKILL_DESCRIPTION_'+name for name in ('baseline', 'hint', 'allowed', 'menu-hidden'))
            if destination == 'messages':
                r['requests'][0]['messages'].append({'role': 'system', 'content': descriptions})
            else:
                r['requests'][0]['tools'].append({'type': 'function', 'function': {'name': 'fixture-catalog', 'description': descriptions}})
            self.assertTrue(evaluate(r)['discovery_verified'])


if __name__ == '__main__':
    unittest.main()
