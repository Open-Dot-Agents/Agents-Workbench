import sys,json,pathlib,tempfile,threading,time,subprocess,argparse
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
sys.path.insert(0,str(pathlib.Path.cwd()/'WORKBENCH/conformance'))
from run_native_approvals import Client,sha,PINS
args=argparse.ArgumentParser();args.add_argument('--scope',choices=['user','root','github'],required=True);args.add_argument('--experimental',action='store_true');opt=args.parse_args()
b=pathlib.Path(tempfile.mkdtemp(prefix='oda-copilot-mcp-',dir='/mnt/DATA/tmp'));h=b/'home';w=b/'workspace';h.mkdir();w.mkdir();subprocess.run(['git','init','-q',str(w)])
server=b/'server.py';server.write_text('''import sys,json,pathlib,os
log=pathlib.Path(sys.argv[1])
for line in sys.stdin:
 request=json.loads(line)
 with log.open('a') as stream:stream.write(json.dumps(request)+'\\n')
 method=request.get('method');identifier=request.get('id')
 if identifier is None:continue
 result={}
 if method=='initialize':result={'protocolVersion':request['params']['protocolVersion'],'capabilities':{'tools':{}},'serverInfo':{'name':'oda-fixture','version':'1'}}
 if method=='tools/list':result={'tools':[{'name':'record','description':'Record isolated fixture data.','inputSchema':{'type':'object','properties':{'marker':{'type':'string'}},'required':['marker']}}]}
 if method=='tools/call':result={'content':[{'type':'text','text':'ODA_MCP_TOOL_RESULT'}]}
 print(json.dumps({'jsonrpc':'2.0','id':identifier,'result':result}),flush=True)
''')
config={'mcpServers':{'oda-fixture':{'type':'local','command':'/usr/bin/python3','args':[str(server),str(b/'mcp-events.jsonl')],'tools':['*'],'deferTools':'never','disableToolCache':True}}}
path={'user':h/'mcp-config.json','root':w/'.mcp.json','github':w/'.github/mcp.json'}[opt.scope];path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(config))
(h/'config.json').write_text(json.dumps({'trustedFolders':[str(w)]}))
requests=[]
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def do_POST(self):
  req=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append(req)
  msg={'role':'assistant','content':'Fixture complete.'};finish='stop'
  if len(requests)==1:
   functions=[t['function'] for t in req.get('tools',[])];matches=[t for t in functions if 'record' in t['name']];print('TOOLS',[t['name'] for t in functions],flush=True)
   if matches:msg={'role':'assistant','content':None,'tool_calls':[{'id':'fixture-mcp-call','type':'function','function':{'name':matches[0]['name'],'arguments':json.dumps({'marker':'ODA_CALL_MARKER'})}}]};finish='tool_calls'
  response={'id':'fixture','object':'chat.completion','created':1,'model':'fixture-model','choices':[{'index':0,'message':msg,'finish_reason':finish}],'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}
  data=json.dumps(response).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
s=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=s.serve_forever,daemon=True).start()
env={'PATH':'/usr/bin:/bin','HOME':str(h),'COPILOT_HOME':str(h),'COPILOT_OFFLINE':'true','COPILOT_PROVIDER_BASE_URL':f'http://127.0.0.1:{s.server_port}/v1','COPILOT_PROVIDER_TYPE':'openai','COPILOT_PROVIDER_WIRE_API':'completions','COPILOT_MODEL':'fixture-model'}
binary='/home/maurizio/.local/bin/copilot';assert sha(binary)==PINS['copilot']
command=[binary,'--acp','--disable-builtin-mcps','--no-auto-update','--no-remote','--allow-tool','oda-fixture(record)']
if opt.experimental:command+=['--experimental']
c=Client(command,w,env,'deny','');r={'fixture':str(b),'native_version':'1.0.83','sha256':sha(binary),'command':command,'source':str(path),'configuration':config};d=time.monotonic()+35
try:
 r['initialize']=c.response(c.request('initialize',{'protocolVersion':1,'clientCapabilities':{}}),d)
 session=c.response(c.request('session/new',{'cwd':str(w),'mcpServers':[]}),d);r['session']=session
 r['prompt']=c.response(c.request('session/prompt',{'sessionId':session['sessionId'],'prompt':[{'type':'text','text':'Call the fixture MCP record tool.'}]}),d)
except Exception as e:r['error']=str(e)
finally:c.close();s.shutdown();s.server_close()
r.update(events=c.events,approvals=c.approvals,stderr=c.errors,requests=requests,mcp_events=(b/'mcp-events.jsonl').read_text() if (b/'mcp-events.jsonl').exists() else '')
p=pathlib.Path('WORKBENCH/evidence/native-draft2-debug/copilot-mcp-session-trusted-state-'+opt.scope+('-experimental' if opt.experimental else '')+'.json');assert not p.exists();p.write_text(json.dumps(r,indent=2)+'\n');p.with_suffix('.runner.py').write_bytes(pathlib.Path(__file__).read_bytes());print('RESULT',r.get('error'),r.get('prompt'),r['mcp_events'])
