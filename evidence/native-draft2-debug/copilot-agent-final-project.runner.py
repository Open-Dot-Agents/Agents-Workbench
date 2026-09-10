#!/usr/bin/env python3
"""Test projected Copilot custom agents with a local deterministic model provider."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from run_native_approvals import Client, PINS, sha


def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--scope',choices=['project','user'],required=True)
 parser.add_argument('--scenario',choices=['execution','no-infer','tool-string','tool-none'],default='execution')
 parser.add_argument('--output',type=Path,required=True)
 args=parser.parse_args();output=args.output.resolve();snapshot=output.with_suffix('.runner.py')
 if output.exists() or snapshot.exists():raise SystemExit('Refuse to replace evidence')
 binary=Path('/home/maurizio/.local/bin/copilot');assert sha(binary)==PINS['copilot']
 repo=Path(__file__).resolve().parents[2];root=Path(tempfile.mkdtemp(prefix='oda-native-agent-',dir='/mnt/DATA/tmp'))
 canonical=root/'canonical';home=root/'home';native=home/'copilot';workspace=canonical if args.scope=='project' else root/'workspace'
 for path in [canonical,home,native,workspace]:path.mkdir(exist_ok=True)
 subprocess.run(['git','init','-q',str(workspace)],check=True)
 marker=root/'effect.txt';probe=root/'effect.py';probe.write_text('from pathlib import Path\nPath('+repr(str(marker))+').write_text("ODA_NATIVE_CHILD_EFFECT")\n')
 manifest=canonical/'.agents';directory=manifest/'native/com.github.copilot';directory.mkdir(parents=True)
 (manifest/'AGENTS.md').write_text('Use fixture data.\n');(manifest/'manifest.json').write_text(json.dumps({'version':'1.1.0-draft.2','profiles':['native']}))
 (directory/'profile.json').write_text(json.dumps({'namespace':'com.github.copilot','harness_version':'=1.0.83','scope':args.scope,'required':True,'artifacts':[{'kind':'agent','source':'fixture.md','name':'oda-fixture.agent.md'}]}))
 tools='bash' if args.scenario=='tool-string' else '[]' if args.scenario=='tool-none' else '[bash]'
 configuration=f'---\nname: ODA Fixture\ndescription: Native fixture agent\ntools: {tools}\nmodel: fixture-model\nreasoningEffort: low\n'
 if args.scenario=='no-infer':configuration+='infer: false\n'
 configuration+='---\nODA_CHILD_DEVELOPER_MARKER\nUse the isolated fixture command.\n'
 (directory/'fixture.md').write_text(configuration);(native/'settings.json').write_text('{"theme":"dark"}\n')
 before={str(p.relative_to(native)):sha(p) for p in native.rglob('*') if p.is_file()}
 cli=root/'agents';subprocess.run(['go','build','-o',str(cli),'./cmd/agents'],cwd=repo/'CLI',check=True)
 env={'PATH':'/usr/bin:/bin','HOME':str(home),'COPILOT_HOME':str(native),'XDG_STATE_HOME':str(root/'state'),'COPILOT_OFFLINE':'true','COPILOT_PROVIDER_TYPE':'openai','COPILOT_PROVIDER_WIRE_API':'completions','COPILOT_MODEL':'fixture-model'}
 command=[str(cli),'apply','--vendor','copilot','--root',str(canonical),'--experimental','--scope',args.scope]
 if args.scope=='user':command+=['--native-home',str(native)]
 apply=subprocess.run(command,env=env,capture_output=True,text=True)
 result={'scope':args.scope,'scenario':args.scenario,'fixture':str(root),'native_version':'1.0.83','native_sha256':sha(binary),'cli_sha256':sha(cli),'runner_sha256':sha(__file__),'configuration':configuration,'apply':{'exit':apply.returncode,'stdout':apply.stdout,'stderr':apply.stderr},'implementation_sha256':{str(p.relative_to(repo)):sha(p) for p in sorted((repo/'CLI/internal/config').glob('native*.go'))},'helper_sha256':sha(repo/'WORKBENCH/conformance/run_native_approvals.py')}
 requests=[];fixture_type=[]
 class Handler(BaseHTTPRequestHandler):
  def log_message(self,*_):pass
  def do_POST(self):
   request=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append(request);n=len(requests)
   message={'role':'assistant','content':'ODA_CHILD_COMPLETED'};finish='stop';call=None
   if n==1:
    task=next((t['function'] for t in request.get('tools',[]) if t['function']['name']=='task'),{})
    types=task.get('parameters',{}).get('properties',{}).get('agent_type',{}).get('enum',[])
    fixture_type.extend(x for x in types if x=='ODA Fixture')
    if fixture_type:call=('task',{'name':'fixture','agent_type':fixture_type[0],'description':'Run isolated fixture','prompt':'Use the isolated fixture command.','mode':'sync'})
   elif n==2:
    # With tools: [], deliberately request the absent Bash tool. The native
    # client must reject the call before it can run the isolated marker script.
    call=('bash',{'command':f'/usr/bin/python3 {probe}','description':'Write isolated fixture marker'})
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
  target=(canonical/'.github' if args.scope=='project' else native)/'agents/oda-fixture.agent.md'
  assert target.read_text()==configuration,'projection changed agent bytes'
  client=Client([str(binary),'--acp','--disable-builtin-mcps','--no-auto-update','--no-remote'],workspace,env,'allow',str(probe));deadline=time.monotonic()+35
  result['initialize']=client.response(client.request('initialize',{'protocolVersion':1,'clientCapabilities':{}}),deadline)
  session=client.response(client.request('session/new',{'cwd':str(workspace),'mcpServers':[]}),deadline);result['session']=session
  result['prompt']=client.response(client.request('session/prompt',{'sessionId':session['sessionId'],'prompt':[{'type':'text','text':'Run the ODA fixture custom agent.'}]}),deadline)
  updates=[e.get('params',{}).get('update',{}) for e in client.events]
  if args.scenario=='no-infer':
   assert not fixture_type,'infer:false agent remains available to automatic delegation'
   assert not marker.exists(),'non-inferred agent executed'
  else:
   assert fixture_type,'agent absent from task schema'
   assert len(requests)>=2 and 'ODA_CHILD_DEVELOPER_MARKER' in json.dumps(requests[1]['messages']),'child instructions absent'
   child_tools={t['function']['name'] for t in requests[1].get('tools',[])};result['child_tools']=sorted(child_tools)
   if args.scenario=='tool-none':
    assert 'bash' not in child_tools,'empty tools list exposes bash'
    assert not marker.exists(),'excluded native tool executed'
    assert any(e.get('toolCallId')=='fixture-call-2' and e.get('status')=='failed' for e in updates),'no correlated tool refusal'
   else:
    assert child_tools=={'bash','read_bash','stop_bash','list_bash'},'unexpected child tools'
    assert marker.read_text()=='ODA_NATIVE_CHILD_EFFECT','child marker absent'
    assert any(e['approved'] and e['params'].get('toolCall',{}).get('toolCallId')=='fixture-call-2' for e in client.approvals),'no correlated approval'
    assert any(e.get('toolCallId')=='fixture-call-2' and e.get('status')=='completed' and e.get('_meta',{}).get('github.com/copilot',{}).get('agentId') for e in updates),'no correlated child command completion'
    assert any(e.get('toolCallId')=='fixture-call-1' and e.get('status')=='completed' and 'ODA_CHILD_COMPLETED' in json.dumps(e) for e in updates),'no correlated delegation result'
  result['passed']=True
 except Exception as error:result.update(passed=False,error=str(error))
 finally:
  if client:client.close();result.update(events=client.events,approvals=client.approvals,stderr=client.errors)
  http.shutdown();http.server_close();result.update(model_requests=requests,marker=marker.read_text() if marker.exists() else '')
  output.parent.mkdir(parents=True,exist_ok=True);snapshot.write_bytes(Path(__file__).read_bytes());output.write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps({'output':str(output),'passed':result['passed'],'error':result.get('error')}))
 return 0 if result['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
