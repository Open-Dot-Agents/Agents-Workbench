#!/usr/bin/env python3
"""Test Copilot hook discovery and execution with isolated native state."""
import argparse
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from run_native_approvals import Client, PINS, sha

HOOK = r'''
import json,os,pathlib,sys,time
log=pathlib.Path(sys.argv[1]);label=sys.argv[2];payload=json.load(sys.stdin)
def record(event):
 with log.open('a') as stream:stream.write(json.dumps(dict(event,label=label,time=time.monotonic()))+'\n')
record({'event':'start','input':payload,'cwd':os.getcwd(),'args':sys.argv[3:],'env':{'ODA_LITERAL':os.getenv('ODA_LITERAL'),'ODA_EXPANDED':os.getenv('ODA_EXPANDED')}})
if label=='timeout':time.sleep(5)
if label=='deny':print(json.dumps({'permissionDecision':'deny','permissionDecisionReason':'ODA_HOOK_DENIED'}))
else:print(json.dumps({'additionalContext':'ODA_HOOK_CONTEXT_'+label}))
record({'event':'end'})
'''

def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--scope',choices=['project','user'],required=True)
 parser.add_argument('--scenario',choices=['execution','deny','timeout','disabled'],default='execution')
 parser.add_argument('--interface',choices=['acp','prompt'],default='acp')
 parser.add_argument('--location',choices=['file','inline'],default='file')
 parser.add_argument('--direct',action='store_true')
 parser.add_argument('--project-opt-in',action='store_true')
 parser.add_argument('--tracked',action='store_true')
 parser.add_argument('--output',type=Path,required=True)
 args=parser.parse_args();output=args.output.resolve();snapshot=output.with_suffix('.runner.py')
 if output.exists() or snapshot.exists():raise SystemExit('Refuse to replace evidence')
 repo=Path(__file__).resolve().parents[2];binary=Path('/home/maurizio/.local/bin/copilot');assert sha(binary)==PINS['copilot']
 root=Path(tempfile.mkdtemp(prefix='oda-native-hooks-',dir='/mnt/DATA/tmp'));workspace=root/'workspace';native=root/'home/copilot';source=root/'source'
 for path in [workspace,native,source]:path.mkdir(parents=True)
 for path in [workspace,source]:subprocess.run(['git','init','-q',str(path)],check=True)
 script=root/'hook.py';script.write_text(HOOK);log=root/'hooks.jsonl';execution=workspace/'execution';execution.mkdir()
 effect=workspace/'effect.txt';probe=workspace/'effect.py';probe.write_text('from pathlib import Path\nPath('+repr(str(effect))+').write_text("ODA_HOOK_TOOL_EFFECT")\n')
 shell=lambda label:shlex.join(['/usr/bin/python3',str(script),str(log),label])
 def direct(label,**extra):return dict(type='command',exec='/usr/bin/python3',args=[str(script),str(log),label,'$HOME * literal'],cwd=str(execution),env={'ODA_LITERAL':'fixture','ODA_EXPANDED':'${ODA_HOOK_INPUT}'},timeoutSec=3,**extra)
 config={'version':1,'hooks':{'sessionStart':[{'type':'command','bash':shell('start'),'command':shell('wrong-fallback')}], 'preToolUse':[direct('pre',matcher='bash'),direct('excluded',matcher='view')], 'postToolUse':[direct('post',matcher='bash')]}}
 if args.scenario in ['deny','timeout']:
  config['hooks']['preToolUse'][0]=direct(args.scenario,matcher='bash')
  if args.scenario=='timeout':config['hooks']['preToolUse'][0].update(timeoutSec=1,timeout=4)
 if args.scenario=='disabled':config['disableAllHooks']=True
 def target(base):
  if args.location=='inline':return base/('.github/copilot/settings.json' if args.scope=='project' else 'settings.json')
  return base/('.github/hooks/fixture.json' if args.scope=='project' else 'hooks/fixture.json')
 content=config if args.location=='file' else {'hooks':config['hooks'],**({'disableAllHooks':True} if args.scenario=='disabled' else {})}
 (native/'config.json').write_text(json.dumps({'trustedFolders':[str(workspace)]}));trust_hash=sha(native/'config.json')
 before={str(p.relative_to(native)):sha(p) for p in native.rglob('*') if p.is_file()}
 cli=root/'agents';subprocess.run(['go','build','-o',str(cli),'./cmd/agents'],cwd=repo/'CLI',check=True)
 env={'PATH':'/usr/bin:/bin','HOME':str(root/'home'),'COPILOT_HOME':str(native),'XDG_STATE_HOME':str(root/'state'),'COPILOT_OFFLINE':'true','COPILOT_PROVIDER_TYPE':'openai','COPILOT_PROVIDER_WIRE_API':'completions','COPILOT_MODEL':'fixture-model','ODA_HOOK_INPUT':'expanded-fixture'}
 if args.project_opt_in:env['GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS']='true'
 result={'interface':args.interface,'project_opt_in':args.project_opt_in,'tracked':args.tracked,'scope':args.scope,'scenario':args.scenario,'location':args.location,'direct':args.direct,'fixture':str(root),'native_version':'1.0.83','native_sha256':sha(binary),'cli_sha256':sha(cli),'runner_sha256':sha(__file__),'helper_sha256':sha(repo/'WORKBENCH/conformance/run_native_approvals.py'),'configuration':content,'implementation_sha256':{str(p.relative_to(repo)):sha(p) for p in sorted((repo/'CLI/internal/config').glob('native*.go'))}}
 target_path=target(workspace if args.scope=='project' else native)
 def run_cli(operation,canonical,home=None):
  command=[str(cli),operation,'--vendor','copilot','--root',str(canonical),'--experimental','--scope',args.scope]
  if home:command+=['--native-home',str(home)]
  run=subprocess.run(command,env=env,capture_output=True,text=True)
  return {'command':command,'exit':run.returncode,'stdout':run.stdout,'stderr':run.stderr}
 requests=[]
 class Handler(BaseHTTPRequestHandler):
  def log_message(self,*_):pass
  def do_POST(self):
   request=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append(request)
   message={'role':'assistant','content':'ODA_HOOK_DONE'};finish='stop'
   if len(requests)==1:
    message={'role':'assistant','content':None,'tool_calls':[{'id':'fixture-hook-tool','type':'function','function':{'name':'bash','arguments':json.dumps({'command':f'/usr/bin/python3 {probe}','description':'Write isolated fixture marker'})}}]};finish='tool_calls'
   response={'id':'fixture','object':'chat.completion','created':1,'model':'fixture-model','choices':[{'index':0,'message':message,'finish_reason':finish}],'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}
   data=json.dumps(response).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
 http=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=http.serve_forever,daemon=True).start();env['COPILOT_PROVIDER_BASE_URL']=f'http://127.0.0.1:{http.server_port}/v1';client=None
 try:
  if args.direct:target_path.parent.mkdir(parents=True,exist_ok=True);target_path.write_text(json.dumps(content))
  else:
   seed=target(source);seed.parent.mkdir(parents=True,exist_ok=True);seed.write_text(json.dumps(content))
   result['import']=run_cli('import',source,source if args.scope=='user' else None);assert result['import']['exit']==0,'import failed'
   shutil.copytree(source/'.agents',workspace/'.agents')
   result['apply']=run_cli('apply',workspace,native if args.scope=='user' else None);assert result['apply']['exit']==0,'apply failed'
  after={str(p.relative_to(native)):sha(p) for p in native.rglob('*') if p.is_file()};result.update(user_files_before_apply=before,user_files_after_apply=after)
  if args.scope=='project':assert after==before,'project apply changed user configuration'
  assert sha(native/'config.json')==trust_hash,'apply changed native trust state'
  assert json.loads(target_path.read_text())==content,'projected hook configuration changed'
  if args.tracked:subprocess.run(['git','add',str(target_path.relative_to(workspace))],cwd=workspace,check=True)
  if args.interface=='prompt':
   command=[str(binary),'--disable-builtin-mcps','--no-auto-update','--no-remote','--allow-tool',f'shell(/usr/bin/python3 {probe})','--allow-tool',f'shell(python3 {probe})','--stream','off','--output-format','json','-p','Run the isolated hook fixture command.'];result['native_command']=command
   run=subprocess.run(command,cwd=workspace,env=env,capture_output=True,text=True,timeout=45)
   result['native_process']={'exit':run.returncode,'stdout':run.stdout,'stderr':run.stderr}
   assert run.returncode==0,'native prompt command failed'
   native_events=[json.loads(line) for line in run.stdout.splitlines() if line.startswith('{')];result['prompt_events']=native_events
   updates=[{'toolCallId':e.get('data',{}).get('toolCallId'),'status':'completed' if e.get('data',{}).get('success') else 'failed'} for e in native_events if e.get('type')=='tool.execution_complete']
  else:
   command=[str(binary),'--acp','--disable-builtin-mcps','--no-auto-update','--no-remote'];result['native_command']=command
   client=Client(command,workspace,env,'allow',str(probe));deadline=time.monotonic()+45
   result['initialize']=client.response(client.request('initialize',{'protocolVersion':1,'clientCapabilities':{}}),deadline)
   session=client.response(client.request('session/new',{'cwd':str(workspace),'mcpServers':[]}),deadline);result['session']=session
   result['prompt']=client.response(client.request('session/prompt',{'sessionId':session['sessionId'],'prompt':[{'type':'text','text':'Run the isolated hook fixture command.'}]}),deadline)
   updates=[e.get('params',{}).get('update',{}) for e in client.events]
  events=[json.loads(line) for line in log.read_text().splitlines()] if log.exists() else [];result['hook_events']=events
  starts=[e for e in events if e['event']=='start'];labels=[e['label'] for e in starts]
  if args.scenario=='disabled':assert not events,'disabled file hooks ran'
  else:
   assert 'start' in labels and 'wrong-fallback' not in labels,'shell precedence mismatch'
   assert 'excluded' not in labels,'matcher included excluded hook'
   chosen=args.scenario if args.scenario in ['deny','timeout'] else 'pre'
   pre=next(e for e in starts if e['label']==chosen)
   assert pre['cwd']==str(execution) and pre['args']==['$HOME * literal'],'exec arguments or working directory changed'
   assert pre['env']=={'ODA_LITERAL':'fixture','ODA_EXPANDED':'${ODA_HOOK_INPUT}'},'hook environment mismatch'
   assert pre['input']['toolName']=='bash' and str(probe) in json.dumps(pre['input']['toolArgs']),'hook input mismatch'
   assert 'ODA_HOOK_CONTEXT_start' in json.dumps(requests[0]['messages']),'session-start context absent'
  if args.scenario=='deny':
   assert not effect.exists() and 'post' not in labels,'denied tool executed'
   assert any(e.get('toolCallId')=='fixture-hook-tool' and e.get('status')=='failed' for e in updates),'no correlated native denial'
   if client:assert not client.approvals,'denial reached approval prompt'
  else:
   assert effect.read_text()=='ODA_HOOK_TOOL_EFFECT','tool effect absent'
   if client:assert any(e['approved'] and e['params'].get('toolCall',{}).get('toolCallId')=='fixture-hook-tool' for e in client.approvals),'no correlated native approval'
   assert any(e.get('toolCallId')=='fixture-hook-tool' and e.get('status')=='completed' for e in updates),'no correlated native completion'
   if args.scenario!='disabled':assert 'post' in labels and 'ODA_HOOK_CONTEXT_post' in json.dumps(requests[-1]['messages']),'post-tool hook result absent'
   if args.scenario=='timeout':
    assert not any(e['event']=='end' and e['label']=='timeout' for e in events),'timed-out hook completed'
    post=next(e for e in starts if e['label']=='post');assert 0.8<post['time']-pre['time']<4,'timeout precedence mismatch'
  result['passed']=True
 except Exception as error:result.update(passed=False,error=str(error))
 finally:
  if client:client.close();result.update(events=client.events,approvals=client.approvals,stderr=client.errors)
  http.shutdown();http.server_close();result.update(model_requests=requests,effect=effect.read_text() if effect.exists() else '')
  if log.exists():result['hook_events']=[json.loads(line) for line in log.read_text().splitlines()]
  snapshot.write_bytes(Path(__file__).read_bytes());output.write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps({'output':str(output),'passed':result['passed'],'error':result.get('error')}));return 0 if result['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
