#!/usr/bin/env python3
"""Verify owning-root import and bounded native ancestor discovery."""
import json
from pathlib import Path

from evidence_state import assess_receipt
from run_native_approvals import PINS,sha
from verify_copilot_skill_import import phase_check

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'WORKBENCH/evidence/native-draft2-debug'
RECEIPT=BASE/'copilot-parent-skills-final.json'


def verify_record(record):
    assert record['passed'] and not record['full_adapter_support'] and not record['provider_errors']
    assert record['native_version']=='1.0.83' and record['native_sha256']==PINS['copilot']
    assert record['runner_sha256']==sha(RECEIPT.with_suffix('.runner.py'))
    assert record['child_did_not_capture_parent'] and record['child_import_preserved_parent'] and record['parent_unchanged_by_child'] and record['source_unchanged']
    assert record['original_metadata']==record['source_metadata_after']
    assert record['original_hashes']==record['imported_hashes']==record['relocated_hashes']
    assert set(record['original_hashes'])=={'SKILL.md','data.txt','blob.bin','scripts/probe.py'}
    assert [p['label'] for p in record['phases']]==['source','relocated','updated','override','fallback','nested-git']
    sessions=set()
    for phase in record['phases']:
        name=phase['label'];present=name!='nested-git'
        origin='project' if name=='override' else 'inherited'
        effect='AGENTS_CHILD_OVERRIDE' if name=='override' else 'AGENTS_PARENT_UPDATED' if name in ('updated','fallback') else 'AGENTS_SKILL_IMPORT_ASSET'
        phase_check(phase,present=present,source=origin,effect=effect)
        prefix=record['fixture']+('/source' if name=='source' else '/target')
        assert phase['workspace']==prefix+'/packages/child'
        suffix='/.github/skills/fixture-import' if name=='source' else '/packages/child/.agents/skills/fixture-import' if name=='override' else '/.agents/skills/fixture-import'
        assert phase['package']==prefix+suffix
        identity=phase['session']['sessionId']
        assert identity not in sessions;sessions.add(identity)
        assert any('AGENTS_PARENT_SKILL_DONE' in json.dumps(e) and identity in json.dumps(e) for e in phase['events'])
    for row in record['commands']:
        assert row['exit_code']==0
        if row['command'][0].endswith('/agents'):assert row['authority_before']==row['authority_after']
    parent=[f for f in record['parent_plan']['native']['features'] if f['feature']=='artifact:skill-discovery:/Parent .github/skills/']
    assert len(parent)==1 and parent[0]['disposition']=='portable-mapping'
    assert parent[0]['source']==parent[0]['destination']==record['fixture']+'/target/.agents/skills'
    external=[f for f in record['child_plan']['native']['features'] if f['feature']=='skills:external-inherited-discovery']
    assert len(external)==1 and external[0]['disposition']=='external'
    assert 'not owned by this projection' in external[0]['ownership']


def verify_discovery():
    path=BASE/'copilot-parent-discovery-probe.json';record=json.loads(path.read_text())
    assert record['native_sha256']==PINS['copilot'] and record['native_version']=='1.0.83'
    assert record['runner_sha256']==sha(path.with_suffix('.runner.py'))
    assert [(c['mode'],c['location']) for c in record['cases']]==[(m,l) for m in ('no-git','parent-git','nested-git') for l in ('parent','child')]
    for case in record['cases']:
        assert case['exit_code']==0
        rows={s['name']:s for s in case['discovery']}
        local=rows['override-fixture']
        assert local['source']=='project' and local['path']==case['cwd']+'/.github/skills/override-fixture'
        present=case['mode']!='nested-git' or case['location']=='parent'
        for origin in ('github','agents','claude'):
            key='parent-'+origin
            assert (key in rows)==present
            if present:
                assert rows[key]['source']==('project' if case['location']=='parent' else 'inherited')
                assert rows[key]['path']==record['fixture']+'/'+case['mode']+'/.'+origin+'/skills/'+key


if __name__=='__main__':
    record=json.loads(RECEIPT.read_text());verify_record(record);verify_discovery()
    helpers={name:ROOT/'WORKBENCH/conformance'/name for name in record['helper_sha256']}
    state=assess_receipt(RECEIPT,ROOT,current_runner=ROOT/'WORKBENCH/conformance/run_native_copilot_parent_skills.py',current_helpers=helpers)
    assert state.integrity_valid,state.integrity_errors
    print('PASS: six correlated native sessions and six discovery probes; parent ownership and nested Git boundary retained; current eligibility: '+json.dumps({'current_eligible':state.current_eligible,'reasons':list(state.eligibility_errors)},sort_keys=True))
