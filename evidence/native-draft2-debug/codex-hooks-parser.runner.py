import sys,json,tempfile,shutil,time
from pathlib import Path
sys.path.insert(0,'/mnt/DATA/workspace/_/Open-Dot-Agents/Open-Dot-Agents/WORKBENCH/conformance')
from run_native_approvals import Client,PINS,sha
repo=Path('/mnt/DATA/workspace/_/Open-Dot-Agents/Open-Dot-Agents');out=repo/'WORKBENCH/evidence/native-draft2-debug/codex-hooks-parser.json';assert not out.exists();binary=Path(shutil.which('codex'));assert sha(binary)==PINS['codex'];root=Path(tempfile.mkdtemp(prefix='oda-hook-parser-',dir='/mnt/DATA/tmp'));records=[]
for case in ['valid','root','event','group','handler','prompt','alias-wrong-type']:
 home=root/case;home.mkdir();(home/'config.toml').write_text('[features]\nhooks=true\n')
 handler={'type':'command','command':'true'};group={'hooks':[handler]};document={'hooks':{'SessionStart':[group]}}
 if case=='root':document['future']=True
 if case=='event':document['hooks']['FutureEvent']=[{'hooks':[{'type':'command','command':'true'}]}]
 if case=='group':group['future']=True
 if case=='handler':handler['future']=True
 if case=='prompt':group['hooks'].append({'type':'prompt'})
 if case=='alias-wrong-type':handler['command_windows']=False
 (home/'hooks.json').write_text(json.dumps(document))
 client=Client([str(binary),'app-server','--stdio'],home,{'PATH':'/usr/bin:/bin','HOME':str(home),'CODEX_HOME':str(home)},'deny','')
 try:
  deadline=time.monotonic()+15;client.response(client.request('initialize',{'clientInfo':{'name':'oda-hook-parser','version':'0.1'},'capabilities':{'experimentalApi':True}}),deadline);client.send({'method':'initialized','params':{}})
  response=client.response(client.request('hooks/list',{'cwds':[str(home)]}),deadline);records.append({'case':case,'configuration':document,'response':response})
 finally:client.close()
 print(case,[(len(x['hooks']),x['warnings'],x['errors']) for x in response['data']],flush=True)
out.write_text(json.dumps({'native_version':'0.154.0','native_sha256':sha(binary),'fixture':str(root),'cases':records,'runner_sha256':sha(__file__)},indent=2)+'\n');out.with_suffix('.runner.py').write_bytes(Path(__file__).read_bytes())
