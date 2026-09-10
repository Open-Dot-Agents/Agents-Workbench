import sys,json,pathlib,tempfile,threading,time,subprocess
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
sys.path.insert(0,str(pathlib.Path.cwd()/'WORKBENCH/conformance'))
from run_native_approvals import Client,sha,PINS
b=pathlib.Path(tempfile.mkdtemp(prefix='oda-copilot-agent-',dir='/mnt/DATA/tmp'));h=b/'home';w=b/'workspace';h.mkdir();w.mkdir();subprocess.run(['git','init','-q',str(w)]);agents=w/'.github/agents';agents.mkdir(parents=True)
(agents/'oda-fixture.agent.md').write_text('---\nname: ODA Fixture\ndescription: Native fixture agent\ntools: [bash]\nmodel: fixture-model\nreasoningEffort: low\n---\nODA_CHILD_DEVELOPER_MARKER\nUse the fixture command.\n')
requests=[]
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def do_POST(self):
  req=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append(req);n=len(requests)
  msg={'role':'assistant','content':'ODA_CHILD_COMPLETED' if n>1 else 'Fixture complete.'};finish='stop'
  call=None
  if n==1:
   task=next(t['function'] for t in req['tools'] if t['function']['name']=='task');print('TASK SCHEMA',json.dumps(task),flush=True)
   types=task['parameters']['properties']['agent_type'].get('enum',[])
   fixture=next((x for x in types if 'oda' in x.lower()),None)
   if fixture:call=('task',{'name':'fixture','agent_type':fixture,'description':'Run isolated fixture','prompt':'Use the fixture command to write the marker.','mode':'sync'})
  elif n==2:
   bash=next((t['function'] for t in req.get('tools',[]) if t['function']['name']=='bash'),None)
   print('CHILD TOOLS',json.dumps(req.get('tools',[])),flush=True)
   if bash:call=('bash',{'command':f"printf ODA_NATIVE_CHILD_EFFECT > {b}/effect.txt",'description':'Write isolated fixture marker'})
  if call:
   msg={'role':'assistant','content':None,'tool_calls':[{'id':f'fixture-call-{n}','type':'function','function':{'name':call[0],'arguments':json.dumps(call[1])}}]};finish='tool_calls'
  response={'id':'fixture','object':'chat.completion','created':1,'model':'fixture-model','choices':[{'index':0,'message':msg,'finish_reason':finish}],'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}
  data=json.dumps(response).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
s=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=s.serve_forever,daemon=True).start()
env={'PATH':'/usr/bin:/bin','HOME':str(h),'COPILOT_HOME':str(h),'COPILOT_OFFLINE':'true','COPILOT_PROVIDER_BASE_URL':f'http://127.0.0.1:{s.server_port}/v1','COPILOT_PROVIDER_TYPE':'openai','COPILOT_PROVIDER_WIRE_API':'completions','COPILOT_MODEL':'fixture-model'}
binary='/home/maurizio/.local/bin/copilot';assert sha(binary)==PINS['copilot']
c=Client([binary,'--acp','--disable-builtin-mcps','--no-auto-update','--no-remote'],w,env,'accept','');r={'fixture':str(b),'native_version':'1.0.83','sha256':sha(binary)};d=time.monotonic()+35
try:
 r['initialize']=c.response(c.request('initialize',{'protocolVersion':1,'clientCapabilities':{}}),d)
 session=c.response(c.request('session/new',{'cwd':str(w),'mcpServers':[]}),d);r['session']=session
 r['prompt']=c.response(c.request('session/prompt',{'sessionId':session['sessionId'],'prompt':[{'type':'text','text':'Run the ODA fixture custom agent.'}]}),d)
except Exception as e:r['error']=str(e)
finally:c.close();s.shutdown();s.server_close()
r.update(events=c.events,stderr=c.errors,requests=requests,marker=(b/'effect.txt').read_text() if (b/'effect.txt').exists() else '')
p=pathlib.Path('WORKBENCH/evidence/native-draft2-debug/copilot-agent-initial.json');assert not p.exists();p.write_text(json.dumps(r,indent=2)+'\n');p.with_suffix('.runner.py').write_bytes(pathlib.Path(__file__).read_bytes());print('RESULT',r.get('error'),r.get('prompt'),r['marker'])
