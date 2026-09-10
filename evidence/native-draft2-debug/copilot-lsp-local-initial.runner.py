import sys,json,pathlib,tempfile,threading,time,subprocess
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
sys.path.insert(0,str(pathlib.Path.cwd()/'WORKBENCH/conformance'))
from run_native_approvals import Client,sha,PINS
b=pathlib.Path(tempfile.mkdtemp(prefix='oda-copilot-local-',dir='/mnt/DATA/tmp'));h=b/'home';w=b/'workspace';h.mkdir();w.mkdir();subprocess.run(['git','init','-q',str(w)])
(w/'test.oda').write_text('fixture_symbol\n')
(h/'lsp-config.json').write_text(json.dumps({'lspServers':{'fixture':{'command':'/usr/bin/python3','args':['/mnt/DATA/tmp/oda-lsp-probe-server.py',str(b/'lsp-events.jsonl')],'fileExtensions':{'.oda':'fixture'},'initializationOptions':{'sentinel':'ODA_LSP'}}}}))
requests=[]
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def do_POST(self):
  req=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append(req)
  tools=req.get('tools',[]);print('TOOLS',json.dumps(tools),flush=True)
  msg={'role':'assistant','content':'Fixture complete.'};finish='stop'
  response={'id':'fixture','object':'chat.completion','created':1,'model':'fixture-model','choices':[{'index':0,'message':msg,'finish_reason':finish}],'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}
  data=json.dumps(response).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
s=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=s.serve_forever,daemon=True).start()
env={'PATH':'/usr/bin:/bin','HOME':str(h),'COPILOT_HOME':str(h),'COPILOT_OFFLINE':'true','COPILOT_PROVIDER_BASE_URL':f'http://127.0.0.1:{s.server_port}/v1','COPILOT_PROVIDER_TYPE':'openai','COPILOT_PROVIDER_WIRE_API':'completions','COPILOT_MODEL':'fixture-model'}
binary='/home/maurizio/.local/bin/copilot';assert sha(binary)==PINS['copilot']
c=Client([binary,'--acp','--disable-builtin-mcps','--no-auto-update','--no-remote'],w,env,'deny','');r={'fixture':str(b),'native_version':'1.0.83','sha256':sha(binary)};d=time.monotonic()+25
try:
 r['initialize']=c.response(c.request('initialize',{'protocolVersion':1,'clientCapabilities':{}}),d)
 session=c.response(c.request('session/new',{'cwd':str(w),'mcpServers':[]}),d);r['session']=session
 r['prompt']=c.response(c.request('session/prompt',{'sessionId':session['sessionId'],'prompt':[{'type':'text','text':'Use LSP to find the definition of fixture_symbol in test.oda.'}]}),d)
except Exception as e:r['error']=str(e)
finally:c.close();s.shutdown();s.server_close()
r.update(events=c.events,stderr=c.errors,requests=requests,lsp_events=(b/'lsp-events.jsonl').read_text() if (b/'lsp-events.jsonl').exists() else '')
p=pathlib.Path('WORKBENCH/evidence/native-draft2-debug/copilot-lsp-local-initial.json');assert not p.exists();p.write_text(json.dumps(r,indent=2)+'\n');p.with_suffix('.runner.py').write_bytes(pathlib.Path(__file__).read_bytes());print('RESULT',r.get('error'),r.get('prompt'),r['lsp_events'])
