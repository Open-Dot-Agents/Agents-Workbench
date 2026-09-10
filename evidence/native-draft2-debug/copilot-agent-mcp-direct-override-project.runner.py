#!/usr/bin/env python3
"""Test Copilot agent-local MCP discovery, delegation, and parent isolation."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from run_native_approvals import Client, PINS, sha
from run_native_copilot_mcp import SERVER


def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--scope',choices=['project','user'],required=True)
 parser.add_argument('--scenario',choices=['isolated','override'],default='isolated')
 parser.add_argument('--direct',action='store_true',help='Write native fixture directly before adapter activation is implemented')
 parser.add_argument('--filename',choices=['oda-fixture.agent.md','oda-fixture.md'],default='oda-fixture.agent.md')
 parser.add_argument('--output',type=Path,required=True)
 args=parser.parse_args();output=args.output.resolve();snapshot=output.with_suffix('.runner.py')
 if output.exists() or snapshot.exists():raise SystemExit('Refuse to replace evidence')
 binary=Path('/home/maurizio/.local/bin/copilot');assert sha(binary)==PINS['copilot']
 repo=Path(__file__).resolve().parents[2];root=Path(tempfile.mkdtemp(prefix='oda-native-agent-',dir='/mnt/DATA/tmp'))
 canonical=root/'canonical';home=root/'home';native=home/'copilot';workspace=canonical if args.scope=='project' else root/'workspace'
 for path in [canonical,home,native,workspace]:path.mkdir(exist_ok=True)
 subprocess.run(['git','init','-q',str(workspace)],check=True)
 server=root/'server.py';server.write_text(SERVER.replace("'ODA_MCP_TOOL_RESULT'", "'ODA_MCP_' + os.getenv('ODA_LITERAL', '')"))
 child_log=root/'child.jsonl';parent_log=root/'parent.jsonl'
 def definition(log,marker):return {'type':'local','command':'/usr/bin/python3','args':[str(server),str(log)],'env':{'ODA_LITERAL':marker},'tools':['record'],'deferTools':'never','disableToolCache':True}
 manifest=canonical/'.agents';directory=manifest/'native/com.github.copilot';directory.mkdir(parents=True)
 (manifest/'AGENTS.md').write_text('Use fixture data.\n');(manifest/'manifest.json').write_text(json.dumps({'version':'1.1.0-draft.2','profiles':['native']}))
 (directory/'profile.json').write_text(json.dumps({'namespace':'com.github.copilot','harness_version':'=1.0.83','scope':args.scope,'required':True,'artifacts':[{'kind':'agent','source':'fixture.md','name':args.filename}]}))
 configuration='---\nname: ODA Fixture\ndescription: Native fixture agent\ntools: ["oda-fixture/*"]\nmcp-servers: '+json.dumps({'oda-fixture':definition(child_log,'CHILD')})+'\n---\nODA_CHILD_DEVELOPER_MARKER\nUse the isolated MCP tool.\n'
 (directory/'fixture.md').write_text(configuration);(native/'settings.json').write_text('{"theme":"dark"}\n')
 (native/'config.json').write_text(json.dumps({'trustedFolders':[str(workspace)]}));trust_hash=sha(native/'config.json')
 if args.scenario=='override':(native/'mcp-config.json').write_text(json.dumps({'mcpServers':{'oda-fixture':definition(parent_log,'PARENT')}}))
 target=(canonical/'.github' if args.scope=='project' else native)/'agents'/args.filename
 before={str(p.relative_to(native)):sha(p) for p in native.rglob('*') if p.is_file()}
 cli=root/'agents';subprocess.run(['go','build','-o',str(cli),'./cmd/agents'],cwd=repo/'CLI',check=True)
 env={'PATH':'/usr/bin:/bin','HOME':str(home),'COPILOT_HOME':str(native),'XDG_STATE_HOME':str(root/'state'),'COPILOT_OFFLINE':'true','COPILOT_PROVIDER_TYPE':'openai','COPILOT_PROVIDER_WIRE_API':'completions','COPILOT_MODEL':'fixture-model'}
 command=[str(cli),'apply','--vendor','copilot','--root',str(canonical),'--experimental','--scope',args.scope]
 if args.scope=='user':command+=['--native-home',str(native)]
 if args.direct:
  target.parent.mkdir(parents=True,exist_ok=True);target.write_text(configuration)
  apply=subprocess.CompletedProcess(command,0,'Direct native fixture; adapter not tested.','')
 else:apply=subprocess.run(command,env=env,capture_output=True,text=True)
 result={'scope':args.scope,'direct':args.direct,'scenario':args.scenario,'fixture':str(root),'native_version':'1.0.83','native_sha256':sha(binary),'cli_sha256':sha(cli),'runner_sha256':sha(__file__),'configuration':configuration,'apply':{'exit':apply.returncode,'stdout':apply.stdout,'stderr':apply.stderr},'implementation_sha256':{str(p.relative_to(repo)):sha(p) for p in sorted((repo/'CLI/internal/config').glob('native*.go'))},'helper_sha256':sha(repo/'WORKBENCH/conformance/run_native_approvals.py')}
 result['mcp_helper_sha256']=sha(repo/'WORKBENCH/conformance/run_native_copilot_mcp.py')
 requests=[];fixture_type=[]
 class Handler(BaseHTTPRequestHandler):
  def log_message(self,*_):pass
  def do_POST(self):
   request=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append(request);n=len(requests)
   message={'role':'assistant','content':'ODA_CHILD_COMPLETED'};finish='stop';call=None
   child='ODA_CHILD_DEVELOPER_MARKER' in json.dumps(request.get('messages',[]))
   prior_calls=[r for r in requests[:-1] if ('ODA_CHILD_DEVELOPER_MARKER' in json.dumps(r.get('messages',[])))==child]
   if child and not prior_calls:call=('oda-fixture-record',{'marker':'ODA_CHILD_CALL'})
   elif not child:
    stage=len(prior_calls)
    if args.scenario=='override' and stage in [0,2]:call=('oda-fixture-record',{'marker':'ODA_PARENT_BEFORE' if stage==0 else 'ODA_PARENT_AFTER'})
    elif stage==(1 if args.scenario=='override' else 0):
     task=next((t['function'] for t in request.get('tools',[]) if t['function']['name']=='task'),{})
     types=task.get('parameters',{}).get('properties',{}).get('agent_type',{}).get('enum',[])
     fixture_type.extend(x for x in types if x=='ODA Fixture')
     if fixture_type:call=('task',{'name':'fixture','agent_type':fixture_type[0],'description':'Run isolated fixture','prompt':'Call the isolated MCP fixture.','mode':'sync'})
   if call:
    message={'role':'assistant','content':None,'tool_calls':[{'id':f'fixture-call-{n}','type':'function','function':{'name':call[0],'arguments':json.dumps(call[1])}}]};finish='tool_calls'
   response={'id':'fixture','object':'chat.completion','created':1,'model':'fixture-model','choices':[{'index':0,'message':message,'finish_reason':finish}],'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}
   data=json.dumps(response).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
 http=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=http.serve_forever,daemon=True).start()
 env['COPILOT_PROVIDER_BASE_URL']=f'http://127.0.0.1:{http.server_port}/v1';client=None
 try:
  assert apply.returncode==0,'projection failed'
  after={str(p.relative_to(native)):sha(p) for p in native.rglob('*') if p.is_file()};result.update(user_files_before_apply=before,user_files_after_apply=after)
  if args.scope=='project':assert after==before,'project projection changed user configuration'
  target=(canonical/'.github' if args.scope=='project' else native)/'agents'/args.filename
  assert target.read_text()==configuration,'projection changed agent bytes'
  assert sha(native/'config.json')==trust_hash,'apply changed native trust state'
  client=Client([str(binary),'--acp','--disable-builtin-mcps','--no-auto-update','--no-remote','--allow-tool','oda-fixture(record)'],workspace,env,'deny','');deadline=time.monotonic()+50
  result['initialize']=client.response(client.request('initialize',{'protocolVersion':1,'clientCapabilities':{}}),deadline)
  session=client.response(client.request('session/new',{'cwd':str(workspace),'mcpServers':[]}),deadline);result['session']=session
  result['prompt']=client.response(client.request('session/prompt',{'sessionId':session['sessionId'],'prompt':[{'type':'text','text':'Run the ODA fixture custom agent.'}]}),deadline)
  updates=[e.get('params',{}).get('update',{}) for e in client.events]
  assert fixture_type,'agent absent from task schema'
  child_requests=[r for r in requests if 'ODA_CHILD_DEVELOPER_MARKER' in json.dumps(r.get('messages',[]))]
  parent_requests=[r for r in requests if r not in child_requests]
  assert len(child_requests)>=2,'child call did not return to model'
  child_tools={t['function']['name'] for t in child_requests[0].get('tools',[])};result['child_tools']=sorted(child_tools)
  assert child_tools=={'oda-fixture-record'},'unexpected child tools'
  assert 'ODA_MCP_CHILD' in json.dumps(child_requests[-1]['messages']),'child result missing'
  child_events=[json.loads(line) for line in child_log.read_text().splitlines()];result['child_mcp_events']=child_events
  child_calls=[e for e in child_events if e.get('method')=='tools/call']
  assert len(child_calls)==1 and child_calls[0]['params']['arguments']['marker']=='ODA_CHILD_CALL','child MCP call mismatch'
  completed=[u for u in updates if u.get('status')=='completed' and 'ODA_MCP_CHILD' in json.dumps(u)]
  assert any(u.get('_meta',{}).get('github.com/copilot',{}).get('agentId') and u.get('toolCallId')=='fixture-call-'+str(requests.index(child_requests[0])+1) for u in completed),'no correlated child completion'
  if args.scenario=='isolated':
   assert all('oda-fixture-record' not in {t['function']['name'] for t in r.get('tools',[])} for r in parent_requests),'agent-only tool exposed to parent'
   assert not parent_log.exists(),'unexpected parent MCP process'
  else:
   parent_events=[json.loads(line) for line in parent_log.read_text().splitlines()];result['parent_mcp_events']=parent_events
   assert [e['params']['arguments']['marker'] for e in parent_events if e.get('method')=='tools/call']==['ODA_PARENT_BEFORE','ODA_PARENT_AFTER'],'parent definition changed after delegation'
   assert all('oda-fixture-record' in {t['function']['name'] for t in r.get('tools',[])} for r in parent_requests),'parent tool disappeared'
   assert 'ODA_MCP_PARENT' in json.dumps(parent_requests[-1]['messages']),'parent result missing'
  result['passed']=True
 except Exception as error:result.update(passed=False,error=str(error))
 finally:
  if client:client.close();result.update(events=client.events,approvals=client.approvals,stderr=client.errors)
  http.shutdown();http.server_close();result.update(model_requests=requests)
  for name,path in [('child_mcp_events',child_log),('parent_mcp_events',parent_log)]:
   if path.exists():result[name]=[json.loads(line) for line in path.read_text().splitlines()]
  output.parent.mkdir(parents=True,exist_ok=True);snapshot.write_bytes(Path(__file__).read_bytes());output.write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps({'output':str(output),'passed':result['passed'],'error':result.get('error')}))
 return 0 if result['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
