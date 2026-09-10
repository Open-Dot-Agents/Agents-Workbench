#!/usr/bin/env python3
"""Audit coverage separately from functional success."""
import argparse
import hashlib
import json
from pathlib import Path

import run_extended


REQUIRED = {
 'stdio-argv': {'native-process','native-mcp-call','stdio-argv-preserved','no-shell-interpolation'},
 'remote-https': {'native-process','native-mcp-call','https-requests','no-literal-auth-in-projection'},
 'remote-auth-env': {'native-process','native-mcp-call','https-requests','no-literal-auth-in-projection'},
 'stdio-env-argv': {'native-process','native-mcp-call','stdio-argv-preserved','stdio-env-resolved','no-shell-interpolation'},
 'hook-lifecycle': {'native-process','native-mcp-call','native-hook-input-json','nonmatching-event-hooks-skipped',
                    *{'event.'+e for e in ['SessionStart','SessionEnd','UserPromptSubmit','PreToolUse','PostToolUse','Stop']}},
 'hook-matchers': {'native-process','native-mcp-call','regex-match','regex-nonmatch'},
 'hook-timeout': {'native-process','native-mcp-call','timeout-started','timeout-stopped-command','one-second-timeout-enforced'},
 'instruction-precedence': {'native-process','native-mcp-call','nearest-instruction-overrides-parent','sibling-scope-isolation'},
 'skill-resources': {'native-process','native-mcp-call','skill-script-used'},
 'profile-tools': {'native-process','initial-off','enabled','removed','enabled-again'},
 'profile-skills': {'native-process','initial-off','enabled','removed','enabled-again'},
 'untrusted': {'native-process','untrusted-hook-not-executed'},
 'refusal-boundaries': {'adapter-refuses-before-writes','no-native-launch'},
 'subagent-events': {'native-process','native-mcp-call','event.SubagentStart','event.SubagentStop','nonmatching-event-hooks-skipped'},
 'permission-request': {'native-process','native-mcp-call','event.PermissionRequest','nonmatching-event-hooks-skipped'},
 'tool-denial': {'native-process','denial-hook-fired','denied-tool-not-executed'},
 'compaction': {'native-process','native-mcp-call','event.PreCompact','nonmatching-event-hooks-skipped'},
 'resumed-refresh': {'native-process','native-mcp-call','resumed-instructions-refreshed','resumed-hooks-refreshed'},
 'missing-env': {'native-process','missing-env-fails-activation'},
 'profile-hooks': {'native-process',*{phase+suffix for phase in ['initial-off','enabled','removed'] for suffix in ['.native-mcp-call','.hook-selection']}},
 'hook-exit-codes': {'native-process','exit2.denies'},
 'resumed-resources': {'native-process','native-mcp-call','resumed-mcp-config-refreshed','resumed-new-skill-resource'},
 'remote-missing-env': {'native-process','missing-header-env-fails-activation','no-empty-or-literal-authorization'},
}
COPILOT_REFUSALS = {'remote-auth-env','stdio-env-argv','missing-env','remote-missing-env'}


def is_refusal(vendor, case):
    return (case == 'refusal-boundaries' or vendor == 'copilot' and case in COPILOT_REFUSALS
            or vendor == 'codex' and case in {'stdio-env-argv','missing-env'})


def required_checks(vendor, case):
    if is_refusal(vendor,case):return {'adapter-refuses-before-writes'} | ({'no-native-launch'} if case=='refusal-boundaries' else set())
    if case=='profile-skills' and vendor in {'codex','copilot'}:
        return {'native-process','adapter-refuses-before-writes','initial-off.refused','enabled','removed.refused','enabled-again'}
    required=set(REQUIRED[case])
    if case=='untrusted':required.add('native-mcp-call' if vendor=='codex' else 'untrusted-mcp-not-loaded')
    if case=='compaction' and vendor=='codex':required.add('native-compaction-completed')
    if case=='hook-exit-codes':required.add('exit1.fail-open' if vendor=='codex' else 'exit1.denies')
    return required


def audit(directory: Path, vendors: list[str]):
    rows=[]
    for vendor in vendors:
        for case in run_extended.cases_for(vendor):
            path=directory/vendor/(case+'.json')
            row={'vendor':vendor,'case':case,'outcome':'missing','valid':False}
            if path.exists():
                try:
                    data=json.loads(path.read_text())
                    assert data['vendor']==vendor and data['case']==case
                    assert data['package']==run_extended.native.VERSIONS['harnesses'][vendor]
                    assert isinstance(data['passed'],bool)
                    checks=data['checks'];assert checks and all(isinstance(c['passed'],bool) for c in checks)
                    assert data['passed']==all(c['passed'] for c in checks)
                    assert required_checks(vendor,case).issubset({c['id'] for c in checks}), 'required assertion missing'
                    if case=='refusal-boundaries':
                        assert sum(c['id']=='adapter-refuses-before-writes' for c in checks)==(7 if vendor=='codex' else 10)
                    expected_native={'instruction-precedence':2,'profile-tools':4,'profile-skills':(4 if vendor=='claude' else 2),'resumed-refresh':2,'resumed-resources':2,'profile-hooks':3,'hook-exit-codes':2}.get(case,1)
                    if is_refusal(vendor,case):expected_native=0
                    if case=='compaction' and vendor=='copilot':expected_native=2
                    assert sum(t['kind']=='native' for t in data['transcripts'])==expected_native, 'native phase missing'
                    refusal=is_refusal(vendor,case)
                    expected_outcome=('adapter-refusal' if refusal else 'native-pass') if data['passed'] else ('runner-incomplete' if any(c['id']=='runner-completed' and not c['passed'] for c in checks) else 'native-failure')
                    assert data['outcome']==expected_outcome, 'outcome contradicts checks'
                    assert data['metadata']['agentsSha256']
                    for name,digest in data['sources'].items():
                        source=directory/'sources'/(digest+'-'+name)
                        assert source.exists() and hashlib.sha256(source.read_bytes()).hexdigest()==digest, 'missing source snapshot'
                    row.update(outcome=data['outcome'],valid=True,failedChecks=[c['id'] for c in checks if not c['passed']])
                except (KeyError,ValueError,AssertionError,TypeError) as error:row.update(outcome='invalid',error=str(error))
            rows.append(row)
    complete=all(r['valid'] and r['outcome'] not in {'missing','invalid','runner-incomplete'} for r in rows)
    passed=complete and all(r['outcome'] in {'native-pass','adapter-refusal'} for r in rows)
    return {'coverageComplete':complete,'functionalPass':passed,'rows':rows}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--result-dir',type=Path,default=run_extended.EVIDENCE)
    parser.add_argument('--vendors',nargs='+',choices=['codex','copilot','claude'],default=['codex','copilot'])
    parser.add_argument('--coverage-only',action='store_true');parser.add_argument('--json',action='store_true')
    args=parser.parse_args();result=audit(args.result_dir,args.vendors)
    if args.json:print(json.dumps(result,indent=2))
    else:
        for row in result['rows']:print(row['vendor'],row['case'],row['outcome'])
        print('coverage complete:',result['coverageComplete'],'functional pass:',result['functionalPass'])
    success=result['coverageComplete'] if args.coverage_only else result['functionalPass']
    return 0 if success else 1


if __name__=='__main__':raise SystemExit(main())
