#!/usr/bin/env python3
"""Verify native key input, editor effects, and keymap lifecycle."""
import json
from pathlib import Path

from evidence_state import assess_receipt
from run_native_approvals import PINS,sha
from run_native_copilot_preferences import plain_terminal

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'WORKBENCH/evidence/native-draft2-debug'
RECEIPTS={'user':'codex-keymap-user-global-action-probe.json','project':'codex-keymap-project-final.json'}


def verify_record(record,path):
    assert record['passed'] and not record['full_adapter_support']
    assert record['native_version']=='0.154.0' and record['native_sha256']==PINS['codex']
    assert record['runner_sha256']==sha(path.with_suffix('.runner.py'))
    assert [p['name'] for p in record['phases']]==['default','override','chord','unbound']
    processes=set()
    keys={'default':[('\x07',True),('\x1b[18~',False)],'override':[('\x07',False),('\x1b[18~',True)],
          'chord':[('\x07',False),('\x1b[18~',False),('\x18\x05',True)],'unbound':[('\x07',False),('\x1b[18~',False),('\x18\x05',False)]}
    bindings={'override':'f7','chord':'ctrl-x ctrl-e','unbound':[]}
    for phase in record['phases']:
        assert phase['pid'] not in processes
        processes.add(phase['pid'])
        assert phase['alive_before_teardown'] and not phase['alive_after_teardown']
        assert plain_terminal(phase['raw_output'])==phase['output']
        expected=[(bytes(k,'ascii').decode('unicode_escape'),v) for k,v in keys[phase['name']]]
        assert [(k['input'],k['expected_effect']) for k in phase['keys']]==expected
        for key in phase['keys']:
            effect=key['effect']
            assert bool(effect)==key['expected_effect']
            if effect:
                assert effect['parent']==phase['pid'] and effect['pid']!=phase['pid']
                assert effect['cwd']==record['fixture']+'/workspace' and effect['time']>=key['start']
                assert 'AGENTS_KEYMAP_EDITED' in phase['output']
        if phase['name']!='default':
            expected={'global':{'open_external_editor':bindings[phase['name']]}}
            assert phase['expected']==phase['projected']==phase['reimported']==expected
    for row in record['commands']:
        assert row['exit_code']==0 and row['authority_before']==row['authority_after']
    adapter=[r['command'] for r in record['commands'] if r['command'][0].endswith('/agents')]
    assert [cmd[1] for cmd in adapter]==['apply','import']*3
    for cmd in adapter:
        assert cmd[cmd.index('--scope')+1]==record['scope']
        assert ('--native-home' in cmd)==(record['scope']=='user')


def verify():
    eligibility={}
    for scope,name in RECEIPTS.items():
        path=BASE/name;record=json.loads(path.read_text())
        assert record['scope']==scope
        verify_record(record,path)
        helpers={name:ROOT/'WORKBENCH/conformance'/name for name in record['helper_sha256']}
        state=assess_receipt(path,ROOT,current_runner=ROOT/'WORKBENCH/conformance/run_native_codex_keymap.py',current_helpers=helpers)
        assert state.integrity_valid,state.integrity_errors
        eligibility[scope]={'current_eligible':state.current_eligible,'reasons':list(state.eligibility_errors)}
    print('PASS: eight native terminal sessions; key replacement, chords, unbinding, and roundtrip preservation; current eligibility: '+json.dumps(eligibility,sort_keys=True))


if __name__=='__main__':verify()
