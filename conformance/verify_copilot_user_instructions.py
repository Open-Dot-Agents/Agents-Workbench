#!/usr/bin/env python3
"""Verify correlated user instruction loading, references, updates, and removal."""
import hashlib
import json
from pathlib import Path

from evidence_state import summarize_receipts

from run_native_approvals import PINS, sha

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT/'WORKBENCH/evidence/native-draft2-debug'


def check_phase(phase):
    assert phase['prompt']['stopReason'] == 'end_turn' and not phase['approvals']
    assert len(phase['requests']) == 2 and phase['nonce'] in json.dumps(phase['requests'])
    context = '\n'.join(m['content'] for m in phase['requests'][0]['messages']
                        if m['role']=='system' and isinstance(m.get('content'), str))
    assert 'AGENTS_PROJECT_BODY' in context and 'AGENTS_PROJECT_REFERENCE_DECOY' not in context
    assert ('AGENTS_USER_FIRST' in context) is (phase['label'] in ('source','relocated'))
    assert ('AGENTS_USER_UPDATED' in context) is (phase['label']=='updated')
    for marker in ('AGENTS_USER_REFERENCE','AGENTS_USER_CHILD'):
        assert (marker in context) is (phase['label']!='removed')
    assert 'AGENTS_FILE_READ' in json.dumps(phase['requests'][1]['messages'])
    events = phase['native_events']
    session = phase['session']['sessionId']
    assert any(e['type']=='session.start' and e['data']['sessionId']==session
               and e['data']['copilotVersion']=='1.0.84-9' and e['data']['context']['cwd']==phase['workspace'] for e in events)
    assert any(e['type']=='user.message' and phase['nonce'] in e['data']['content'] for e in events)
    reads = [e['data'] for e in events if e['type']=='tool.execution_start']
    assert len(reads)==1 and reads[0]['toolName']=='view'
    assert reads[0]['arguments']=={'path':str(Path(phase['workspace'])/'fixture.txt')}
    assert any(e['type']=='tool.execution_complete' and e['data']['toolCallId']==reads[0]['toolCallId']
               and e['data']['success'] is True and 'AGENTS_FILE_READ' in e['data']['result']['content'] for e in events)
    assert any(e['type']=='assistant.turn_end' for e in events)
    assert any(e.get('method')=='session/update' and e['params']['sessionId']==session for e in phase['events'])


def verify():
    implementation = {str(p.relative_to(ROOT)):sha(p) for p in (ROOT/'CLI/internal/config').glob('*.go')}
    sources = json.loads((BASE/'copilot-user-instructions.sources.json').read_text())['sources']
    for source in sources: assert hashlib.sha256(source['body'].encode()).hexdigest()==source['sha256']
    assert '$HOME/.copilot/copilot-instructions.md' in sources[1]['body']
    sessions = []
    receipts=[]
    for case in ('default-home','custom-home','custom-source'):
        path = BASE/f'copilot-user-instructions-{case}-final.json'
        receipts.append(path)
        r = json.loads(path.read_text())
        assert r['passed'] and not r['full_adapter_support'] and r['case']==case and r['scope']=='user'
        assert r['native_version']=='1.0.84-9' and r['native_sha256']==PINS['copilot']
        assert r['runner_sha256']==sha(path.with_suffix('.runner.py'))
        assert all(r[k] for k in ('roundtrip_preserved','source_unchanged','authority_unchanged','references_unchanged','project_unchanged'))
        assert r['target_mode']==0o600 and r['plan']['applicable']
        assert len(r['authority_checks'])==6 and all(c['before']==c['after'] for c in r['authority_checks'])
        assert r['native_trust']=={label:[str(Path(r['fixture'])/label)] for label in ('source','target')}
        assert [c['command'][1] for c in r['commands']]==['build','import','plan','apply','import','apply','apply']
        assert all(c['exit_code']==0 for c in r['commands']) and '--backup' in r['commands'][-1]['command']
        assert [p['label'] for p in r['phases']]==['source','relocated','updated','removed']
        for phase in r['phases']:
            check_phase(phase)
            sessions.append(phase['session']['sessionId'])
        feature = next(f for f in r['plan']['native']['features'] if f['feature']=='instructions')
        assert feature['native_status']=='bounded-user-instruction-loading' and feature['scope']=='user'
        expected = 'policy/local.md' if case=='custom-source' else 'copilot-instructions.md'
        assert feature['source']==str(Path(r['fixture'])/'target/.agents/native/com.github.copilot'/expected)
        assert feature['destination']==str(Path(r['phases'][1]['native_home'])/'copilot-instructions.md')
        assert any('referenced user instruction files' in action for action in r['plan']['native']['required_native_actions'])
    assert len(set(sessions))==12
    path = BASE/'copilot-user-instructions-custom-source-before.json'
    before = json.loads(path.read_text())
    assert not before['passed'] and not before['roundtrip_preserved'] and len(before['phases'])==2
    assert before['runner_sha256']==sha(path.with_suffix('.runner.py'))
    for phase in before['phases']: check_phase(phase)
    assert before['commands'][-1]['command'][1]=='import' and before['commands'][-1]['exit_code']==0
    duplicate = json.loads((BASE/'copilot-user-instructions-source-duplication.json').read_text())
    assert duplicate['native_receipt_sha256']==sha(path)
    assert duplicate['before']['artifacts']==[{'kind':'instructions','source':'policy/local.md'}]
    assert duplicate['after']['artifacts']==[{'kind':'instructions','source':'policy/local.md'},{'kind':'instructions','source':'copilot-instructions.md'}]
    path = BASE/'copilot-user-instructions-custom-source-probe.json'
    probe = json.loads(path.read_text())
    assert not probe['passed'] and probe['roundtrip_preserved'] and not probe['authority_unchanged']
    assert probe['runner_sha256']==sha(path.with_suffix('.runner.py')) and len(probe['phases'])==4
    for phase in probe['phases']: check_phase(phase)
    for name in ('copilot-user-instructions-source-before','copilot-user-instructions-test-lock-first'):
        path = BASE/(name+'.json')
        record = json.loads(path.read_text())
        assert record['exit_code']!=0 and record['test_sha256']==sha(path.with_suffix('.test.go'))
    state=summarize_receipts(receipts,ROOT,current_runner=ROOT/'WORKBENCH/conformance/run_native_copilot_user_instructions.py',
                             current_helper=ROOT/'WORKBENCH/conformance/run_native_approvals.py')
    assert state['historical_integrity'],state['integrity_errors']
    print(json.dumps({'passed':True,'native_cases':3,**state,'full_adapter_support':False},indent=2))


if __name__=='__main__':
    verify()
