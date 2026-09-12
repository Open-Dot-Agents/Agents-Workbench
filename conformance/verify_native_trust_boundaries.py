#!/usr/bin/env python3
"""Verify trust-derived unattended and compound command observations."""
import json
from pathlib import Path
from evidence_state import summarize_receipts
from run_native_approvals import PINS,sha

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'WORKBENCH/evidence/native-draft2-debug'
FILES={'unattended':'codex-trust-boundary-unattended-counterexample.json','composed-deny':'codex-trust-boundary-composed.json','background-deny':'codex-trust-boundary-background.json'}
FILES.update({'pending-timeout':'codex-trust-boundary-pending-timeout.json','disconnect':'codex-trust-boundary-disconnect.json'})
FILES['post-start-disconnect']='codex-trust-boundary-post-start-cleanup.json'

def verify_record(record,path):
    case=record['case']
    assert record['passed'] and not record['full_adapter_support'] and not record['portable_security_projection_tested']
    assert record['native_version']=='0.154.0' and record['native_sha256']==PINS['codex']
    assert record['runner_sha256']==sha(path.with_suffix('.runner.py'))
    assert record['approval_policy']=='omitted; trust-derived untrusted'
    assert record['config_unchanged'] and record['requests']
    assert all(r['model']=='fixture-model' and 'ODA_DISABLED_PROJECT_MODEL' not in json.dumps(r) for r in record['requests'])
    command=record['command_under_test']
    assert str(Path(record['fixture'])/'workspace/first.py') in command
    if case=='unattended':
        assert record['effective_thread']['approvalPolicy']=='untrusted' and record['effective_thread']['model']=='fixture-model'
        assert record['outcome']=='native-unattended-approval-bypass'
        completed=[e['item'] for e in record['native_events'] if e.get('type')=='item.completed' and e.get('item',{}).get('type')=='command_execution']
        assert len(completed)==1 and completed[0]['status']=='completed' and completed[0]['exit_code']==0
        assert record['first_effect'] and int(record['first_effect'])>1 and record['second_effect'] is None and record['native_exit_code']==0
    elif case in ('composed-deny','background-deny'):
        operator=' && ' if case=='composed-deny' else ' & '
        assert operator in command and str(Path(record['fixture'])/'workspace/second.py') in command
        assert record['outcome']=='verified-composed-denial-before-descendants'
        assert record['thread']['approvalPolicy']=='untrusted' and record['thread']['model']=='fixture-model'
        relevant=[a for a in record['approvals'] if a['params'].get('itemId')=='trust-boundary-call']
        assert len(relevant)==1 and not relevant[0]['approved'] and relevant[0]['response']['decision']=='decline'
        terminal=[e['params']['item'] for e in record['events'] if e.get('method')=='item/completed' and e.get('params',{}).get('item',{}).get('id')=='trust-boundary-call']
        assert len(terminal)==1 and terminal[0]['status']=='declined'
        assert record['first_effect'] is None and record['second_effect'] is None
        assert record['completion']['params']['turn']['status']=='completed'
    elif case=='post-start-disconnect':
        assert record['thread']['approvalPolicy']=='untrusted' and record['thread']['model']=='fixture-model'
        assert record['outcome']=='detached-background-descendant-terminated-at-command-completion'
        assert record['first_effect_before_disconnect'] and record['first_effect']==record['first_effect_before_disconnect']
        assert record['second_effect_before_disconnect'] is None and record['second_effect'] is None
        assert record['command_completed_before_disconnect'] and record['native_exit_after_disconnect'] is not None
        relevant=[a for a in record['approvals'] if a['params'].get('itemId')=='trust-boundary-call']
        assert len(relevant)==1 and relevant[0]['approved'] and relevant[0]['response']['decision']=='accept'
        terminal=[e['params']['item'] for e in record['events'] if e.get('method')=='item/completed' and e.get('params',{}).get('item',{}).get('id')=='trust-boundary-call']
        assert len(terminal)==1 and terminal[0]['status']=='completed'
    else:
        assert record['thread']['approvalPolicy']=='untrusted' and record['thread']['model']=='fixture-model'
        pending=record['pending_request']
        assert pending['method']=='item/commandExecution/requestApproval' and pending['params']['itemId']=='trust-boundary-call'
        assert record['observation_seconds']>=4.9 and record['native_alive_after_window']
        assert record['first_effect_after_window'] is None and record['second_effect_after_window'] is None
        assert record['first_effect'] is None and record['second_effect'] is None
        relevant=[a for a in record['approvals'] if a['params'].get('itemId')=='trust-boundary-call']
        assert len(relevant)==1 and not relevant[0]['approved']
        if case=='pending-timeout':
            assert record['outcome']=='no-native-approval-timeout-in-observation-window'
            assert relevant[0]['response']=={'decision':'decline'}
            terminal=[e['params']['item'] for e in record['events'] if e.get('method')=='item/completed' and e.get('params',{}).get('item',{}).get('id')=='trust-boundary-call']
            assert len(terminal)==1 and terminal[0]['status']=='declined'
            assert record['completion']['params']['turn']['status']=='completed'
        else:
            assert record['outcome']=='disconnect-before-approval-response-prevents-execution'
            assert relevant[0]['response'] is None and record['native_exit_after_disconnect'] is not None

def verify():
    receipts=[]
    for case,name in FILES.items():
        path=BASE/name;receipts.append(path);record=json.loads(path.read_text());assert record['case']==case;verify_record(record,path)
    state=summarize_receipts(receipts,ROOT,current_runner=ROOT/'WORKBENCH/conformance/run_native_trust_boundaries.py',
                             current_helper=ROOT/'WORKBENCH/conformance/run_native_approvals.py')
    assert state['historical_integrity'],state['integrity_errors']
    print(json.dumps({'passed':True,'native_cases':len(receipts),**state,'full_adapter_support':False},indent=2))

if __name__=='__main__':verify()
