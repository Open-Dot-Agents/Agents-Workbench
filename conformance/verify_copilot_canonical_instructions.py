#!/usr/bin/env python3
"""Verify one canonical source, distinct native bodies, relocation, and updated loading."""
import hashlib
import json
from pathlib import Path

from evidence_state import summarize_receipts

from run_native_approvals import PINS, sha
from verify_copilot_root_instructions import check_phase

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT/'WORKBENCH/evidence/native-draft2-debug'


def check_canonical_phase(phase, case):
    check_phase(phase, 'distinct' if case == 'link-distinct' else 'reference')
    context = '\n'.join(m['content'] for m in phase['requests'][0]['messages']
                        if m['role'] == 'system' and isinstance(m.get('content'), str))
    assert 'AGENTS_REFERENCED_POLICY' in context
    assert 'AGENTS_CANONICAL_REFERENCE_DECOY' not in context and 'AGENTS_WRONG_REFERENCE_BASE' not in context
    assert ('AGENTS_NATIVE_REFERENCED_POLICY' in context) is (case == 'link-distinct')
    assert ('AGENTS_UPDATED_CORE_MARKER' in context) is (phase['label'] == 'updated')


def verify():
    sources = json.loads((BASE/'copilot-canonical-instructions.sources.json').read_text())['sources']
    assert [s['url'] for s in sources] == [
        'https://docs.github.com/llms.txt',
        'https://docs.github.com/api/article/body?pathname=/en/copilot/how-tos/copilot-cli/customize-copilot/add-custom-instructions',
    ]
    for source in sources:
        assert hashlib.sha256(source['body'].encode()).hexdigest() == source['sha256']
    implementation = {str(p.relative_to(ROOT)): sha(p) for p in (ROOT/'CLI/internal/config').glob('*.go')}
    receipts=[]
    for case in ('link-only', 'link-distinct'):
        path = BASE/f'copilot-canonical-instructions-{case}-user-instructions.json'
        receipts.append(path)
        r = json.loads(path.read_text())
        assert r['passed'] and not r['full_adapter_support'] and r['case'] == case
        assert r['native_version'] == '1.0.84-9' and r['native_sha256'] == PINS['copilot']
        assert r['runner_sha256'] == sha(path.with_suffix('.runner.py'))
        assert r['source_unchanged'] and r['authority_unchanged'] and r['source_link_preserved'] and r['roundtrip_preserved']
        assert [p['label'] for p in r['phases']] == ['source', 'relocated', 'updated']
        for phase in r['phases']: check_canonical_phase(phase, case)
        assert r['plan']['applicable']
        bindings = [f for f in r['plan']['native']['features'] if f['feature'] == 'canonical-instructions']
        assert len(bindings) == 1 and bindings[0]['source'] == str(Path(r['fixture'])/'target/.agents/AGENTS.md')
        assert bindings[0]['destination'] == str(Path(r['fixture'])/'target/AGENTS.md')
        assert [c['command'][1] for c in r['commands']] == ['build', 'import', 'apply', 'plan', 'apply', 'import', 'apply', 'import']
        assert all(c['exit_code'] == 0 for c in r['commands'])
        assert '--adopt' in r['commands'][2]['command']
    path = BASE/'copilot-canonical-instructions-before.json'
    before = json.loads(path.read_text())
    assert not before['passed'] and before['native_sha256'] == PINS['copilot']
    assert before['runner_sha256'] == sha(path.with_suffix('.runner.py'))
    check_canonical_phase(before['phases'][0], 'link-distinct')
    assert before['commands'][-1]['exit_code'] != 0 and 'distinct project instructions' in before['commands'][-1]['stderr']
    state=summarize_receipts(receipts,ROOT,current_runner=ROOT/'WORKBENCH/conformance/run_native_copilot_canonical_instructions.py',
                             current_helper=ROOT/'WORKBENCH/conformance/run_native_approvals.py')
    assert state['historical_integrity'],state['integrity_errors']
    print(json.dumps({'passed':True,'native_cases':2,**state,'full_adapter_support':False},indent=2))


if __name__ == '__main__':
    verify()
