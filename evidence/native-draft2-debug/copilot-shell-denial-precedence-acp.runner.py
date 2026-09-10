#!/usr/bin/env python3
"""Probe pinned Copilot shell rules with a deterministic local model.

Path grants are explicit test inputs. No portable security mapping is applied.
The provider requests only the generated fixture command, once.
"""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from run_native_approvals import Client, PINS, sha
import time


def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--command',choices=['python-absolute','python-name','touch-absolute','touch-name','compound','compound-mixed'],default='python-absolute')
 parser.add_argument('--rule',choices=['absolute-exact','basename-exact','absolute-prefix','basename-prefix','absolute-stem','basename-stem','all','none','all-deny-exact','all-deny-stem','all-deny-other','all-deny-basename'],required=True)
 parser.add_argument('--alternate-operand',action='store_true')
 parser.add_argument('--paths',choices=['all','workspace','executable'],default='all')
 parser.add_argument('--hook',choices=['none','context'],default='none')
 parser.add_argument('--interface',choices=['prompt','acp'],default='prompt')
 parser.add_argument('--expect',choices=['allow','deny'],required=True)
 parser.add_argument('--output',type=Path,required=True)
 args=parser.parse_args();output=args.output.resolve();snapshot=output.with_suffix('.runner.py')
 if output.exists() or snapshot.exists():raise SystemExit('Refuse to replace evidence')
 binary=Path('/home/maurizio/.local/bin/copilot');assert sha(binary)==PINS['copilot']
 root=Path(tempfile.mkdtemp(prefix='oda-native-shell-',dir='/mnt/DATA/tmp'));workspace=root/'workspace';home=root/'home';native=home/'copilot'
 workspace.mkdir();native.mkdir(parents=True);subprocess.run(['git','init','-q',str(workspace)],check=True)
 marker=workspace/'effect.txt';second=workspace/'second.txt';probe=workspace/'probe.py';probe.write_text('from pathlib import Path\nPath('+repr(str(second if args.command=='compound-mixed' else marker))+').write_text("ODA_SHELL_EFFECT")\n')
 absolute='/usr/bin/python3' if args.command.startswith('python') else '/usr/bin/touch'
 executable=absolute if args.command.endswith('absolute') or args.command.startswith('compound') else Path(absolute).name
 operand=str(probe) if args.command.startswith('python') else str(marker)
 operation=shlex.join([executable,operand])
 if args.alternate_operand:
  assert args.command.startswith('python'),'alternate operand requires a Python command'
  alternate=workspace/'alternate.py';alternate.write_bytes(probe.read_bytes());operation=shlex.join([executable,str(alternate)])
 if args.command=='compound':operation+=' && '+shlex.join(['/usr/bin/touch',str(second)])
 if args.command=='compound-mixed':operation+=' && '+shlex.join(['/usr/bin/python3',str(probe)])
 stem=absolute if args.rule.startswith('absolute') else Path(absolute).name
 pattern='shell('+shlex.join([stem,operand])+')' if args.rule.endswith('exact') else 'shell('+stem+':*)'
 if args.rule.endswith('stem'):pattern='shell('+stem+')'
 flags=[]
 if args.rule=='all':flags=['--allow-tool','shell']
 elif args.rule=='all-deny-other':flags=['--allow-tool','shell','--deny-tool','shell('+shlex.join([absolute,str(workspace/'not-requested.py')])+')']
 elif args.rule=='all-deny-basename':flags=['--allow-tool','shell','--deny-tool','shell('+Path(absolute).name+')']
 elif args.rule=='all-deny-stem':flags=['--allow-tool','shell','--deny-tool','shell('+absolute+')']
 elif args.rule=='all-deny-exact':flags=['--allow-tool','shell','--deny-tool','shell('+shlex.join([absolute,operand])+')']
 elif args.rule!='none':flags=['--allow-tool',pattern]
 (native/'config.json').write_text(json.dumps({'trustedFolders':[str(workspace)]}))
 if args.hook=='context':
  hook=workspace/'hook.py';hook.write_text('import json,sys\njson.load(sys.stdin)\nprint(json.dumps({"additionalContext":"ODA_RULE_HOOK_CONTEXT"}))\n')
  hook_dir=workspace/'.github/hooks';hook_dir.mkdir(parents=True)
  (hook_dir/'fixture.json').write_text(json.dumps({'version':1,'hooks':{'preToolUse':[{'type':'command','exec':'/usr/bin/python3','args':[str(hook)],'matcher':'bash'}]}}))

 env={'PATH':'/usr/bin:/bin','HOME':str(home),'COPILOT_HOME':str(native),'COPILOT_OFFLINE':'true','COPILOT_PROVIDER_TYPE':'openai','COPILOT_PROVIDER_WIRE_API':'completions','COPILOT_MODEL':'fixture-model'}
 requests=[]
 class Handler(BaseHTTPRequestHandler):
  def log_message(self,*_):pass
  def do_POST(self):
   request=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append(request)
   message={'role':'assistant','content':'Fixture done.'};finish='stop'
   if len(requests)==1:
    message={'role':'assistant','content':None,'tool_calls':[{'id':'fixture-shell-call','type':'function','function':{'name':'bash','arguments':json.dumps({'command':operation,'description':'Run isolated shell-rule fixture'})}}]};finish='tool_calls'
   response={'id':'fixture','object':'chat.completion','created':1,'model':'fixture-model','choices':[{'index':0,'message':message,'finish_reason':finish}],'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}
   data=json.dumps(response).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
 http=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=http.serve_forever,daemon=True).start();env['COPILOT_PROVIDER_BASE_URL']=f'http://127.0.0.1:{http.server_port}/v1'
 common=[str(binary),'--disable-builtin-mcps','--no-auto-update','--no-remote','--no-remote-export','--no-bash-env','--no-custom-instructions',*(['--allow-all-paths'] if args.paths=='all' else ['--add-dir','/usr/bin'] if args.paths=='executable' else []),'--log-level','debug',*flags]
 result={'fixture':str(root),'case':vars(args)|{'output':str(output)},'native_version':'1.0.83','native_sha256':sha(binary),'runner_sha256':sha(__file__),'helper_sha256':sha(Path(__file__).with_name('run_native_approvals.py')),'operation':operation,'probe_sha256':sha(probe),'full_adapter_support':False,'portable_security_projection_tested':False};client=None
 try:
  if args.interface=='prompt':
   command=common+['--output-format','json','--stream','off','-p','Run the isolated shell-rule fixture once.'];result['command']=command
   run=subprocess.run(command,cwd=workspace,env=env,capture_output=True,text=True,timeout=45);result['process']={'exit':run.returncode,'stdout':run.stdout,'stderr':run.stderr}
   assert run.returncode==0,'native process failed'
   events=[json.loads(line) for line in run.stdout.splitlines() if line.startswith('{')];result['native_events']=events
   started=[e for e in events if e.get('type')=='tool.execution_start' and e.get('data',{}).get('toolCallId')=='fixture-shell-call']
   terminal=[e['data'] for e in events if e.get('type')=='tool.execution_complete' and e.get('data',{}).get('toolCallId')=='fixture-shell-call']
   assert len(started)==1 and started[0]['data']['arguments']['command']==operation,'native command correlation mismatch'
   assert len(terminal)==1,'no single correlated terminal event'
   executed=terminal[0].get('success') is True
   denied=terminal[0].get('success') is False and terminal[0].get('error',{}).get('code')=='denied'
  else:
   command=common+['--acp'];result['command']=command;client=Client(command,workspace,env,'deny',str(probe));deadline=time.monotonic()+45
   result['initialize']=client.response(client.request('initialize',{'protocolVersion':1,'clientCapabilities':{}}),deadline)
   session=client.response(client.request('session/new',{'cwd':str(workspace),'mcpServers':[]}),deadline);result['session']=session
   result['prompt']=client.response(client.request('session/prompt',{'sessionId':session['sessionId'],'prompt':[{'type':'text','text':'Run the isolated shell-rule fixture once.'}]}),deadline)
   updates=[e.get('params',{}).get('update',{}) for e in client.events]
   terminal=[e for e in updates if e.get('toolCallId')=='fixture-shell-call' and e.get('status') in ['completed','failed']]
   assert len(terminal)==1,'no single correlated terminal event'
   executed=terminal[0]['status']=='completed';denied=terminal[0]['status']=='failed' and terminal[0].get('rawOutput',{}).get('code') in ['denied','rejected']
   result['approvals']=client.approvals
  assert sha(probe)==result['probe_sha256'],'fixture script changed'
  result.update(observed='allow' if executed else 'deny' if denied else 'inconclusive',marker=marker.exists(),second_marker=second.exists())
  if not (client and denied):assert len(requests)>=2,'tool result did not return to model'
  if client and denied:assert result['prompt']['stopReason']=='end_turn','ACP denial did not end the turn'
  assert (executed and marker.exists()) or (denied and not marker.exists() and not second.exists()),'native event and effects disagree'
  if args.command.startswith('compound') and executed:assert second.exists(),'second composed command did not execute'
  assert result['observed']==args.expect,'native shell-rule outcome differs from expectation'
  result['passed']=True
 except Exception as error:result.update(passed=False,error=str(error))
 finally:
  if client:client.close();result.update(events=client.events,approvals=client.approvals,stderr=client.errors)
  http.shutdown();http.server_close();result['model_requests']=requests
  result['native_logs']={p.name:p.read_text() for p in sorted((native/'logs').glob('*.log'))}
  snapshot.write_bytes(Path(__file__).read_bytes());output.write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps({'output':str(output),'passed':result['passed'],'observed':result.get('observed'),'error':result.get('error')}));return 0 if result['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
