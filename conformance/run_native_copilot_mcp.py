#!/usr/bin/env python3
"""Import and project MCP configuration, then test it with pinned Copilot.

Only an isolated fixture config.json receives native folder trust. Apply must
leave it unchanged. The deterministic provider and MCP server are local.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from run_native_approvals import Client, PINS, sha, native_binary

SERVER=r'''
import sys,json,pathlib,os,time
log=pathlib.Path(sys.argv[1])
def record(value):
 with log.open('a') as stream:stream.write(json.dumps(dict(value,_recorded_at=time.monotonic()))+'\n')
record({'event':'process','cwd':os.getcwd(),'env':{'ODA_LITERAL':os.getenv('ODA_LITERAL'),'ODA_ENV':os.getenv('ODA_ENV')}})
for line in sys.stdin:
 request=json.loads(line);record(request)
 method=request.get('method');identifier=request.get('id')
 if identifier is None:continue
 result={}
 if method=='initialize':result={'protocolVersion':request['params']['protocolVersion'],'capabilities':{'tools':{}},'serverInfo':{'name':'oda-fixture','version':'1'}}
 if method=='tools/list':result={'tools':[{'name':name,'description':'Record isolated fixture data.','inputSchema':{'type':'object','properties':{'marker':{'type':'string'}},'required':['marker']}} for name in ['record','blocked']]}
 if method=='tools/call':
  result={'content':[{'type':'text','text':'ODA_MCP_TOOL_RESULT'}]}
 print(json.dumps({'jsonrpc':'2.0','id':identifier,'result':result}),flush=True)
'''

def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--scope',choices=['project','user'],required=True)
 parser.add_argument('--scenario',choices=['call','blocked','expansion','http'],default='call')
 parser.add_argument('--output',type=Path,required=True)
 args=parser.parse_args();output=args.output.resolve();snapshot=output.with_suffix('.runner.py')
 if output.exists() or snapshot.exists():raise SystemExit('Refuse to replace evidence')
 binary=native_binary('copilot');assert sha(binary)==PINS['copilot']
 repo=Path(__file__).resolve().parents[2];root=Path(tempfile.mkdtemp(prefix='oda-native-mcp-'))
 source=root/'source';workspace=root/'workspace';home=root/'home';native=home/'copilot';canonical=workspace
 for path in [source,workspace,home,native]:path.mkdir(exist_ok=True)
 for path in [source,workspace]:subprocess.run(['git','init','-q',str(path)],check=True)
 execution=workspace/'execution';execution.mkdir();server=root/'server.py';server.write_text(SERVER);events_path=root/'mcp-events.jsonl'
 config={'mcpServers':{'oda-fixture':{'type':'local','command':'/usr/bin/python3','args':[str(server),str(events_path)],'tools':['record'],'deferTools':'never','disableToolCache':True,'timeout':3000,'env':{'ODA_LITERAL':'fixture','ODA_ENV':'${ODA_MCP_INPUT}'},'cwd':str(execution)}}}
 if args.scenario=='expansion':config['mcpServers']['oda-fixture'].update(command='${ODA_MCP_PYTHON}',args=['${ODA_MCP_SERVER}',str(events_path)])
 remote_http=None
 if args.scenario=='http':
  class MCPHandler(BaseHTTPRequestHandler):
   def log_message(self,*_):pass
   def do_GET(self):self.send_response(405);self.send_header('Content-Length','0');self.end_headers()
   def do_POST(self):
    request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
    with events_path.open('a') as stream:stream.write(json.dumps(dict(request,_headers={'X-ODA-Environment':self.headers.get('X-ODA-Environment'),'X-ODA-Literal':self.headers.get('X-ODA-Literal')}))+'\n')
    identifier=request.get('id');method=request.get('method');result={}
    if identifier is None:self.send_response(202);self.send_header('Content-Length','0');self.end_headers();return
    if method=='initialize':result={'protocolVersion':request['params']['protocolVersion'],'capabilities':{'tools':{}},'serverInfo':{'name':'oda-fixture','version':'1'}}
    if method=='tools/list':result={'tools':[{'name':name,'description':'Record isolated fixture data.','inputSchema':{'type':'object','properties':{'marker':{'type':'string'}},'required':['marker']}} for name in ['record','blocked']]}
    if method=='tools/call':result={'content':[{'type':'text','text':'ODA_MCP_TOOL_RESULT'}]}
    data=json.dumps({'jsonrpc':'2.0','id':identifier,'result':result}).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
  remote_http=ThreadingHTTPServer(('127.0.0.1',0),MCPHandler);threading.Thread(target=remote_http.serve_forever,daemon=True).start()
  config={'mcpServers':{'oda-fixture':{'type':'http','url':f'http://127.0.0.1:{remote_http.server_port}/mcp','tools':['record'],'headers':{'X-ODA-Environment':'${ODA_MCP_INPUT}','X-ODA-Literal':'fixture'},'deferTools':'never','disableToolCache':True,'timeout':3000}}}
 seed=source/('.mcp.json' if args.scope=='project' else 'mcp-config.json');seed.write_text(json.dumps(config))
 # This is isolated native test setup, not an adapter projection.
 (native/'config.json').write_text(json.dumps({'trustedFolders':[str(workspace)]}))
 (native/'settings.json').write_text('{"theme":"dark"}\n');state_hash=sha(native/'config.json')
 before={str(p.relative_to(native)):sha(p) for p in native.rglob('*') if p.is_file()}
 cli=root/'agents';subprocess.run(['go','build','-o',str(cli),'./cmd/agents'],cwd=repo/'CLI',check=True)
 env={'PATH':'/usr/bin:/bin','HOME':str(home),'COPILOT_HOME':str(native),'XDG_STATE_HOME':str(root/'state'),'COPILOT_OFFLINE':'true','COPILOT_PROVIDER_TYPE':'openai','COPILOT_PROVIDER_WIRE_API':'completions','COPILOT_MODEL':'fixture-model','ODA_MCP_INPUT':'expanded-fixture','ODA_MCP_PYTHON':'/usr/bin/python3','ODA_MCP_SERVER':str(server)}
 import_root=source if args.scope=='project' else canonical
 command=[str(cli),'import','--vendor','copilot','--root',str(import_root),'--experimental','--scope',args.scope]
 if args.scope=='user':command+=['--native-home',str(source)]
 imported=subprocess.run(command,env=env,capture_output=True,text=True)
 if args.scope=='project' and imported.returncode==0:shutil.copytree(source/'.agents',canonical/'.agents')
 command=[str(cli),'apply','--vendor','copilot','--root',str(canonical),'--experimental','--scope',args.scope]
 if args.scope=='user':command+=['--native-home',str(native)]
 applied=subprocess.run(command,env=env,capture_output=True,text=True)
 def result_of(run):return {'exit':run.returncode,'stdout':run.stdout,'stderr':run.stderr}
 result={'scope':args.scope,'scenario':args.scenario,'fixture':str(root),'native_version':'1.0.83','native_sha256':sha(binary),'cli_sha256':sha(cli),'runner_sha256':sha(__file__),'helper_sha256':sha(repo/'WORKBENCH/conformance/run_native_approvals.py'),'configuration':config,'import':result_of(imported),'apply':result_of(applied),'implementation_sha256':{str(p.relative_to(repo)):sha(p) for p in sorted((repo/'CLI/internal/config').glob('native*.go'))}}
 requests=[]
 class Handler(BaseHTTPRequestHandler):
  def log_message(self,*_):pass
  def do_POST(self):
   request=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append(request)
   message={'role':'assistant','content':'Fixture complete.'};finish='stop'
   if len(requests)==1:
    name='oda-fixture-blocked' if args.scenario=='blocked' else 'oda-fixture-record'
    message={'role':'assistant','content':None,'tool_calls':[{'id':'fixture-mcp-call','type':'function','function':{'name':name,'arguments':json.dumps({'marker':'ODA_CALL_MARKER'})}}]};finish='tool_calls'
   response={'id':'fixture','object':'chat.completion','created':1,'model':'fixture-model','choices':[{'index':0,'message':message,'finish_reason':finish}],'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}
   data=json.dumps(response).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
 http=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=http.serve_forever,daemon=True).start()
 env['COPILOT_PROVIDER_BASE_URL']=f'http://127.0.0.1:{http.server_port}/v1';client=None
 try:
  assert imported.returncode==0 and applied.returncode==0,'import or projection failed'
  assert sha(native/'config.json')==state_hash,'apply changed native trust state'
  result['trust_state_sha256']=state_hash
  after={str(p.relative_to(native)):sha(p) for p in native.rglob('*') if p.is_file()};result.update(user_files_before_apply=before,user_files_after_apply=after)
  if args.scope=='project':assert before==after,'project apply changed user configuration'
  target=workspace/'.mcp.json' if args.scope=='project' else native/'mcp-config.json'
  result['projected_configuration']=json.loads(target.read_text());assert result['projected_configuration']==config,'MCP values changed'
  command=[str(binary),'--acp','--disable-builtin-mcps','--no-auto-update','--no-remote','--allow-tool','oda-fixture(record)'];result['native_command']=command
  client=Client(command,workspace,env,'deny','');deadline=time.monotonic()+35
  result['initialize']=client.response(client.request('initialize',{'protocolVersion':1,'clientCapabilities':{}}),deadline)
  session=client.response(client.request('session/new',{'cwd':str(workspace),'mcpServers':[]}),deadline);result['session']=session
  result['prompt']=client.response(client.request('session/prompt',{'sessionId':session['sessionId'],'prompt':[{'type':'text','text':'Call the isolated fixture MCP tool.'}]}),deadline)
  events=[json.loads(line) for line in events_path.read_text().splitlines()];result['mcp_events']=events
  if args.scenario=='http':
   assert all(e['_headers']=={'X-ODA-Environment':'expanded-fixture','X-ODA-Literal':'fixture'} for e in events),'HTTP header expansion mismatch'
  else:
   process=next(e for e in events if e.get('event')=='process');assert process['cwd']==str(execution) and process['env']=={'ODA_LITERAL':'fixture','ODA_ENV':'expanded-fixture'},'server context mismatch'
  assert any(e.get('method')=='initialize' for e in events) and any(e.get('method')=='tools/list' for e in events),'missing MCP handshake'
  tools={t['function']['name'] for t in requests[0].get('tools',[])}
  assert 'oda-fixture-record' in tools and 'oda-fixture-blocked' not in tools,'MCP tools filter mismatch'
  updates=[e.get('params',{}).get('update',{}) for e in client.events]
  if args.scenario=='blocked':
   assert not any(e.get('method')=='tools/call' for e in events),'excluded MCP tool reached the server'
   assert any(e.get('toolCallId')=='fixture-mcp-call' and e.get('status')=='failed' for e in updates),'no correlated native tool refusal'
  else:
   call=next(e for e in events if e.get('method')=='tools/call');assert call['params']['name']=='record' and call['params']['arguments']['marker']=='ODA_CALL_MARKER'
   assert any(e.get('toolCallId')=='fixture-mcp-call' and e.get('status')=='completed' and 'ODA_MCP_TOOL_RESULT' in json.dumps(e) for e in updates),'no correlated native completion'
   assert len(requests)>=2 and 'ODA_MCP_TOOL_RESULT' in json.dumps(requests[-1]['messages']),'MCP result absent from model input'
  result['passed']=True
 except Exception as error:result.update(passed=False,error=str(error))
 finally:
  if client:client.close();result.update(events=client.events,approvals=client.approvals,stderr=client.errors)
  http.shutdown();http.server_close();result['model_requests']=requests
  if remote_http:remote_http.shutdown();remote_http.server_close()
  if events_path.exists():result['mcp_events']=[json.loads(line) for line in events_path.read_text().splitlines()]
  snapshot.write_bytes(Path(__file__).read_bytes());output.write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps({'output':str(output),'passed':result['passed'],'error':result.get('error')}));return 0 if result['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
