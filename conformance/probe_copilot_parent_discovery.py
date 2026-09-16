#!/usr/bin/env python3
"""Record native parent skill discovery without importing ancestor content."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile

from run_native_approvals import PINS,sha, native_binary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();output=args.output.resolve();snapshot=output.with_suffix('.runner.py')
    assert not output.exists() and not snapshot.exists(),'refuse evidence replacement'
    binary=native_binary('copilot')
    assert sha(binary)==PINS['copilot']
    with snapshot.open('xb') as f:f.write(Path(__file__).read_bytes())
    base=Path(tempfile.mkdtemp(prefix='agents-parent-discovery-'))
    home=base/'home';home.mkdir(mode=0o700)
    env={'HOME':str(home),'COPILOT_HOME':str(home/'.copilot'),'COPILOT_CACHE_HOME':str(base/'cache'),
         'XDG_STATE_HOME':str(base/'state'),'PATH':'/usr/bin:/bin','COPILOT_OFFLINE':'true'}
    result={'native_version':'1.0.84-9','native_sha256':sha(binary),'runner_sha256':sha(snapshot),'fixture':str(base),'cases':[],
            'full_adapter_support':False,'adapter_mapping':False}
    for mode in ('no-git','parent-git','nested-git'):
        parent=base/mode;child=parent/'packages/child';child.mkdir(parents=True)
        if mode!='no-git':subprocess.run(['git','init','-q',str(parent)],check=True)
        if mode=='nested-git':subprocess.run(['git','init','-q',str(child)],check=True)
        for origin in ('.github','.agents','.claude'):
            package=parent/origin/'skills'/('parent-'+origin[1:]);package.mkdir(parents=True)
            (package/'SKILL.md').write_text('---\nname: parent-'+origin[1:]+'\ndescription: Parent fixture.\n---\nPARENT_'+origin[1:].upper()+'\n')
        for level,directory in [('parent',parent),('child',child)]:
            package=directory/'.github/skills/override-fixture';package.mkdir(parents=True)
            (package/'SKILL.md').write_text('---\nname: override-fixture\ndescription: '+level+' fixture.\n---\n'+level+'\n')
        for location,workspace in [('parent',parent),('child',child)]:
            command=[str(binary),'skill','list','--json']
            p=subprocess.run(command,cwd=workspace,env=env,capture_output=True,text=True,timeout=40)
            row={'mode':mode,'location':location,'cwd':str(workspace),'command':command,'exit_code':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
            if p.returncode==0:
                try:row['discovery']=json.loads(p.stdout)
                except ValueError as error:row['parse_error']=str(error)
            result['cases'].append(row)
            print(mode,location,p.returncode,[(s['name'],s['source'],s['path']) for s in row.get('discovery',[])])
    with output.open('x') as f:json.dump(result,f,indent=2);f.write('\n')


if __name__=='__main__':main()
