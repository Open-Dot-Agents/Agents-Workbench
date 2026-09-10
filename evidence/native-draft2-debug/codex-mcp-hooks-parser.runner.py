import json,sys,time,shutil
from pathlib import Path
repo=Path('/mnt/DATA/workspace/_/Open-Dot-Agents/Open-Dot-Agents')
sys.path.insert(0,str(repo/'WORKBENCH/conformance'))
from run_native_approvals import Client,PINS,sha
base=repo/'WORKBENCH/evidence/native-draft2-debug'
output=base/'codex-mcp-hooks-parser.json';snapshot=output.with_suffix('.runner.py')
assert not output.exists() and not snapshot.exists()
previous=json.loads((base/'codex-mcp-hooks-first-project.json').read_text());root=Path(previous['fixture']);home=root/'home';workspace=root/'workspace'
binary=Path(shutil.which('codex'));assert sha(binary)==PINS['codex']
cases=[('object',{'nested':[{'value':True},7,'${tool_input}',0.5]}),('null',{'value':None}),('nested-null',{'outer':{'value':None}}),('array-null',{'value':[None]}),('uint64',{'value':18446744073709551615}),('above-int64',{'value':9223372036854775808}),('int64',{'value':9223372036854775807}),('negative-int64',{'value':-9223372036854775808}),('float',{'value':1e100}),('session-end',{'value':'fixture'})]
result={'native_version':'0.154.0','native_sha256':sha(binary),'runner_sha256':sha(__file__),'helper_sha256':sha(repo/'WORKBENCH/conformance/run_native_approvals.py'),'fixture':str(root),'cases':[],'full_adapter_support':False}
for name,inputs in cases:
 event='SessionEnd' if name=='session-end' else 'PreToolUse'
 config={'hooks':{event:[{'hooks':[{'type':'mcp_tool','server':'oda_hook','tool':'record','input':inputs}]}]}}
 (workspace/'.codex/hooks.json').write_text(json.dumps(config))
 c=Client([str(binary),'app-server','--stdio'],workspace,{'PATH':'/usr/bin:/bin','HOME':str(home),'CODEX_HOME':str(home)},'deny','')
 try:
  deadline=time.monotonic()+15
  c.response(c.request('initialize',{'clientInfo':{'name':'oda-parser','version':'1'},'capabilities':{'experimentalApi':True}}),deadline);c.send({'method':'initialized','params':{}})
  response=c.response(c.request('hooks/list',{'cwds':[str(workspace)]}),deadline)
  result['cases'].append({'case':name,'configuration':config,'response':response})
 except Exception as e:result['cases'].append({'case':name,'error':str(e)})
 finally:c.close()
snapshot.write_bytes(Path(__file__).read_bytes());output.write_text(json.dumps(result,indent=2)+'\n')
for c in result['cases']:
 print(c['case'],json.dumps(c.get('response',{})))
