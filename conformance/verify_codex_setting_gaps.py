#!/usr/bin/env python3
"""Verify ignored and rejected settings without treating raw parsing as support."""
import json
from pathlib import Path

from evidence_state import assess_receipt
from run_native_approvals import PINS, sha

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'WORKBENCH/evidence/native-draft2-debug'
NAMES={'baseline':'baseline','reasoning-false':'reasoning-false','reasoning-invalid':'reasoning-invalid',
       'rollout-control':'rollout-control-complete','rollout-interval':'rollout-interval-complete',
       'mcp-local':'mcp-local-final','mcp-remote':'mcp-remote-final','keymap-invalid':'keymap-invalid'}


def verify_record(record,path,completed):
    assert record['native_version']=='0.154.0' and record['native_sha256']==PINS['codex']
    assert record['runner_sha256']==sha(path.with_suffix('.runner.py'))
    assert not record['full_adapter_support'] and not record['adapter_mapping']
    assert record['config_unchanged'] and not record['provider_errors'] and not record['approvals']
    assert record['native_completed']==completed
    if completed:
        identity=record['thread']['thread']['id']
        assert record['completion']['params']['threadId']==identity
        assert record['completion']['params']['turn']['status']=='completed'
        assert record['model_requests'] and all('AGENTS_SETTING_PROBE_REQUEST' in json.dumps(q) for q in record['model_requests'])
        assert any('AGENTS_SETTING_PROBE_DONE' in json.dumps(e) and identity in json.dumps(e) for e in record['events'])
        assert all(q['reasoning']=={'effort':'medium','summary':'auto'} for q in record['model_requests'])
    else:
        expected='tui.keymap.global.open_external_editor' if record['case']=='keymap-invalid' else 'features.rollout_budget'
        assert expected in record['error'] and 'invalid configuration' in record['error']
        assert record['model_requests']==[]
    if record['case'].startswith('mcp-'):
        effect=record['mcp_effect']
        assert effect['parent']==record['native_pid'] and effect['pid']!=record['native_pid']
        assert effect['cwd']==record['fixture']+'/workspace'
        assert any(e.get('method')=='mcpServer/startupStatus/updated' and e['params']['name']=='fixture' and e['params']['status']=='ready'
                   and e['params']['threadId']==record['thread']['thread']['id'] for e in record['events'])


def verify():
    schema=json.loads((ROOT/'CLI/internal/config/native_schemas/codex-0.154.0.json').read_text())
    assert 'model_supports_reasoning_summaries' not in schema['properties']
    assert 'experimental_environment' not in schema['definitions']['RawMcpServerConfig']['properties']
    assert 'reminder_interval_tokens' not in schema['definitions']['RolloutBudgetConfigToml']['properties']
    records={};eligibility={}
    for case,name in NAMES.items():
        path=BASE/('codex-setting-'+name+'.json')
        record=json.loads(path.read_text());records[case]=record
        assert record['case']==case
        verify_record(record,path,case not in ('rollout-interval','keymap-invalid'))
        state=assess_receipt(path,ROOT,current_runner=ROOT/'WORKBENCH/conformance/run_native_codex_setting_gaps.py',
                             current_helper=ROOT/'WORKBENCH/conformance/run_native_approvals.py')
        assert state.integrity_valid,state.integrity_errors
        eligibility[case]={'current_eligible':state.current_eligible,'reasons':list(state.eligibility_errors)}
    assert 'model_supports_reasoning_summaries=false' in records['reasoning-false']['config']
    assert 'model_supports_reasoning_summaries="invalid-type"' in records['reasoning-invalid']['config']
    assert 'experimental_environment="remote"' in records['mcp-remote']['config']
    assert 'experimental_environment' not in records['mcp-local']['config']
    assert 'reminder_at_remaining_tokens=[100]' in records['rollout-control']['config']
    assert 'reminder_interval_tokens=100' in records['rollout-interval']['config']
    print('PASS: six correlated native turns and two configuration refusals; three pinned native limitations; current eligibility: '+json.dumps(eligibility,sort_keys=True))


if __name__=='__main__':verify()
