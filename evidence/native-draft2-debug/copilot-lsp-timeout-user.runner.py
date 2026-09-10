#!/usr/bin/env python3
"""Test projected Copilot LSP with isolated files and a local deterministic model.

No account or credential files are copied. The native CLI performs LSP discovery,
process launch, initialization, and the tool call. No server install is performed.
"""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from run_native_approvals import Client, PINS, sha

SERVER = r'''
import json,os,pathlib,sys,time
log=pathlib.Path(sys.argv[1])
def record(value):
 with log.open('a') as stream: stream.write(json.dumps(dict(value,_recorded_at=time.monotonic()))+'\n')
record({'event':'process','pid':os.getpid(),'mode':os.environ.get('ODA_LSP_MODE'),'cwd':os.getcwd()})
while True:
 headers={}
 while True:
  line=sys.stdin.buffer.readline()
  if not line: sys.exit(0)
  if line in (b'\n',b'\r\n'): break
  key,value=line.decode().split(':',1);headers[key.lower()]=value.strip()
 message=json.loads(sys.stdin.buffer.read(int(headers['content-length'])))
 record(message)
 if 'id' in message:
  result=None
  if message.get('method')=='initialize':result={'capabilities':{'textDocumentSync':1,'hoverProvider':True},'serverInfo':{'name':'oda-fixture','version':'1'}}
  if message.get('method')=='textDocument/hover' and len(sys.argv)>2 and sys.argv[2] in ('timeout','delayed-hover'):time.sleep(5)
  if message.get('method')=='textDocument/hover':result={'contents':{'kind':'plaintext','value':'ODA_NATIVE_HOVER_EFFECT'}}
  data=json.dumps({'jsonrpc':'2.0','id':message['id'],'result':result}).encode()
  sys.stdout.buffer.write(f'Content-Length: {len(data)}\r\n\r\n'.encode()+data);sys.stdout.buffer.flush()
'''

def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--scope',choices=['project','user'],required=True)
 parser.add_argument('--scenario',choices=['hover','timeout','delayed-hover'],default='hover')
 parser.add_argument('--output',type=Path,required=True)
 parser.add_argument('--binary',type=Path,default=Path('/home/maurizio/.local/bin/copilot'))
 args=parser.parse_args();output=args.output.resolve();snapshot=output.with_suffix('.runner.py')
 if output.exists() or snapshot.exists():raise SystemExit('Refuse to replace evidence')
 assert sha(args.binary)==PINS['copilot'],'native binary pin mismatch'
 repo=Path(__file__).resolve().parents[2];root=Path(tempfile.mkdtemp(prefix='oda-native-lsp-',dir='/mnt/DATA/tmp'))
 canonical=root/'canonical';home=root/'home';native=home/'copilot';workspace=canonical if args.scope=='project' else root/'workspace'
 for path in [canonical,home,native,workspace]:path.mkdir(exist_ok=True)
 subprocess.run(['git','init','-q',str(workspace)],check=True)
 package=workspace/'packages/frontend';package.mkdir(parents=True);source=package/'test.oda';source.write_text('fixture_symbol\n')
 server=root/'server.py';server.write_text(SERVER);events_path=root/'lsp-events.jsonl'
 manifest=canonical/'.agents';directory=manifest/'native/com.github.copilot';directory.mkdir(parents=True)
 (manifest/'AGENTS.md').write_text('Use fixture data.\n');(manifest/'manifest.json').write_text(json.dumps({'version':'1.1.0-draft.2','profiles':['native']}))
 (directory/'profile.json').write_text(json.dumps({'namespace':'com.github.copilot','harness_version':'=1.0.83','scope':args.scope,'required':True,'artifacts':[{'kind':'lsp','source':'lsp.json'}]}))
 config={'lspServers':{'fixture':{'command':'${ODA_LSP_PYTHON}','args':[str(server),'${ODA_LSP_EVENTS}',args.scenario],'fileExtensions':{'.oda':'fixture'},'env':{'ODA_LSP_MODE':'${ODA_LSP_INPUT}'},'initializationOptions':{'sentinel':'ODA_LSP','exact':9007199254740993},'rootUri':'packages/frontend','requestTimeoutMs':1000 if args.scenario=='timeout' else 30000}}}
 (directory/'lsp.json').write_text(json.dumps(config));(native/'settings.json').write_text('{"theme":"dark"}\n')
 initial_home={str(p.relative_to(native)):sha(p) for p in native.rglob('*') if p.is_file()}
 cli=root/'agents';subprocess.run(['go','build','-o',str(cli),'./cmd/agents'],cwd=repo/'CLI',check=True)
 env={'PATH':'/usr/bin:/bin','HOME':str(home),'COPILOT_HOME':str(native),'XDG_STATE_HOME':str(root/'state'),'COPILOT_OFFLINE':'true','COPILOT_PROVIDER_TYPE':'openai','COPILOT_PROVIDER_WIRE_API':'completions','COPILOT_MODEL':'fixture-model','ODA_LSP_PYTHON':'/usr/bin/python3','ODA_LSP_EVENTS':str(events_path),'ODA_LSP_INPUT':'expanded-fixture'}
 command=[str(cli),'apply','--vendor','copilot','--root',str(canonical),'--experimental','--scope',args.scope]
 if args.scope=='user':command+=['--native-home',str(native)]
 apply=subprocess.run(command,env=env,capture_output=True,text=True)
 result={'scenario':args.scenario,'scope':args.scope,'fixture':str(root),'native_version':'1.0.83','native_sha256':sha(args.binary),'cli_sha256':sha(cli),'runner_sha256':sha(__file__),'helper_sha256':sha(repo/'WORKBENCH/conformance/run_native_approvals.py'),'configuration':config,'apply':{'exit':apply.returncode,'stdout':apply.stdout,'stderr':apply.stderr},'implementation_sha256':{str(p.relative_to(repo)):sha(p) for p in sorted((repo/'CLI/internal/config').glob('native*.go'))}}
 requests=[]
 class Handler(BaseHTTPRequestHandler):
  def log_message(self,*_):pass
  def do_POST(self):
   request=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append(request)
   message={'role':'assistant','content':'Fixture complete.'};finish='stop'
   if len(requests)==1:
    available=any(t.get('function',{}).get('name')=='lsp' for t in request.get('tools',[]))
    if available:
     message={'role':'assistant','content':None,'tool_calls':[{'id':'fixture-lsp-call','type':'function','function':{'name':'lsp','arguments':json.dumps({'operation':'hover','file':str(source),'line':1,'character':1})}}]};finish='tool_calls'
   response={'id':'fixture','object':'chat.completion','created':1,'model':'fixture-model','choices':[{'index':0,'message':message,'finish_reason':finish}],'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}
   data=json.dumps(response).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
 http=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=http.serve_forever,daemon=True).start()
 env['COPILOT_PROVIDER_BASE_URL']=f'http://127.0.0.1:{http.server_port}/v1';client=None
 try:
  assert apply.returncode==0,'projection failed'
  after_apply={str(p.relative_to(native)):sha(p) for p in native.rglob('*') if p.is_file()}
  result['user_files_before_apply']=initial_home;result['user_files_after_apply']=after_apply
  if args.scope=='project':assert after_apply==initial_home,'project apply changed user configuration'
  target=canonical/'.github/lsp.json' if args.scope=='project' else native/'lsp-config.json'
  result['projected_configuration']=json.loads(target.read_text());assert result['projected_configuration']==config
  client=Client([str(args.binary),'--acp','--disable-builtin-mcps','--no-auto-update','--no-remote'],workspace,env,'deny','');deadline=time.monotonic()+35
  result['initialize']=client.response(client.request('initialize',{'protocolVersion':1,'clientCapabilities':{}}),deadline)
  session=client.response(client.request('session/new',{'cwd':str(workspace),'mcpServers':[]}),deadline);result['session']=session
  result['prompt']=client.response(client.request('session/prompt',{'sessionId':session['sessionId'],'prompt':[{'type':'text','text':'Use LSP hover on fixture_symbol in packages/frontend/test.oda.'}]}),deadline)
  result['prompt_completed_at']=time.monotonic()
  lsp=[json.loads(line) for line in events_path.read_text().splitlines()];result['lsp_events']=lsp
  process=next(e for e in lsp if e.get('event')=='process');assert process['mode']=='expanded-fixture','env expansion mismatch'
  initialize=next(e for e in lsp if e.get('method')=='initialize')['params']
  assert initialize['rootUri']==package.as_uri(),'rootUri mismatch'
  assert initialize['initializationOptions']==config['lspServers']['fixture']['initializationOptions'],'initialization options changed'
  opened=next(e for e in lsp if e.get('method')=='textDocument/didOpen')['params']['textDocument'];assert opened['languageId']=='fixture' and opened['uri']==source.as_uri()
  hover=next(e for e in lsp if e.get('method')=='textDocument/hover');assert hover['params']['textDocument']['uri']==source.as_uri()
  calls=[e.get('params',{}).get('update',{}) for e in client.events]
  if args.scenario!='timeout':
   assert any(e.get('toolCallId')=='fixture-lsp-call' and e.get('status')=='completed' and 'ODA_NATIVE_HOVER_EFFECT' in json.dumps(e) for e in calls),'no correlated completed native event'
   assert len(requests)>=2 and 'ODA_NATIVE_HOVER_EFFECT' in json.dumps(requests[-1]['messages']),'hover result absent from model input'
  else:
   completed=[e for e in calls if e.get('toolCallId')=='fixture-lsp-call' and e.get('status') in ('completed','failed')]
   assert completed and any('No hover information available' in json.dumps(e) for e in completed),'no correlated empty hover result'
   elapsed=result['prompt_completed_at']-hover['_recorded_at'];result['hover_elapsed_seconds']=elapsed
   assert 0.5<elapsed<4,'completion does not precede the five-second response delay'
   assert len(requests)>=2 and 'ODA_NATIVE_HOVER_EFFECT' not in json.dumps(requests[-1]['messages']),'delayed hover incorrectly returned'
  result['passed']=True
 except Exception as error:result['passed']=False;result['error']=str(error)
 finally:
  if client:client.close();result.update(events=client.events,stderr=client.errors)
  http.shutdown();http.server_close();result['model_requests']=requests
  if events_path.exists():result['lsp_events']=[json.loads(line) for line in events_path.read_text().splitlines()]
  output.parent.mkdir(parents=True,exist_ok=True);snapshot.write_bytes(Path(__file__).read_bytes());output.write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps({'output':str(output),'passed':result['passed'],'error':result.get('error')}))
 return 0 if result['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
