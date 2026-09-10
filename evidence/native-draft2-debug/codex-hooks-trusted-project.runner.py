#!/usr/bin/env python3
"""Test pinned Codex native hook loading with an isolated local Responses provider."""
import argparse,json,shlex,shutil,subprocess,tempfile,threading,time
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from run_native_approvals import Client,PINS,sha

HOOK=r'''
import json,os,pathlib,sys,time
payload=json.load(sys.stdin)
def record(event):
 with pathlib.Path(sys.argv[1]).open('a') as out:out.write(json.dumps({'event':event,'label':sys.argv[2],'input':payload,'cwd':os.getcwd(),'time':time.monotonic()})+'\n')
record('start')
if sys.argv[2]=='timeout':time.sleep(5)
response={'hookEventName':payload['hook_event_name'],'additionalContext':'ODA_CODEX_HOOK_'+sys.argv[2]}
if sys.argv[2]=='deny':response.update(permissionDecision='deny',permissionDecisionReason='ODA_CODEX_HOOK_DENIED')
print(json.dumps({'hookSpecificOutput':response,'systemMessage':'ODA_HOOK_STATUS_'+sys.argv[2]}))
record('end')
'''

def toml(v):
 if isinstance(v,dict):return '{'+', '.join(json.dumps(k)+' = '+toml(x) for k,x in v.items())+'}'
 if isinstance(v,list):return '['+', '.join(toml(x) for x in v)+']'
 return json.dumps(v)

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--scope',choices=['project','user'],required=True)
 p.add_argument('--location',choices=['file','inline'],default='file');p.add_argument('--direct',action='store_true')
 p.add_argument('--trust',choices=['unreviewed','bypass','preconfigured'],default='preconfigured');p.add_argument('--scenario',choices=['execution','deny','timeout','disabled-field'],default='execution')
 p.add_argument('--output',type=Path,required=True);args=p.parse_args();output=args.output.resolve();snapshot=output.with_suffix('.runner.py')
 if output.exists() or snapshot.exists():raise SystemExit('Refuse to replace evidence')
 binary=Path(shutil.which('codex'));assert sha(binary)==PINS['codex'];repo=Path(__file__).resolve().parents[2]
 root=Path(tempfile.mkdtemp(prefix='oda-codex-hooks-',dir='/mnt/DATA/tmp'));home=root/'home';workspace=root/'workspace';source=root/'source'
 for path in [home,workspace,source]:path.mkdir(mode=0o700)
 for path in [workspace,source]:subprocess.run(['git','init','-q',str(path)],check=True)
 hook=root/'hook.py';hook.write_text(HOOK);log=root/'hooks.jsonl';probe=workspace/'probe.py';effect=workspace/'effect.txt';probe.write_text('from pathlib import Path\nPath('+repr(str(effect))+').write_text("ODA_CODEX_TOOL_EFFECT")\n')
 def group(label,matcher):return {'matcher':matcher,'hooks':[{'type':'command','command':shlex.join(['/usr/bin/python3',str(hook),str(log),label]),'timeout':3,'statusMessage':'ODA_LOADING_'+label,'additionalContextLimit':0,'async':False}]}
 config={'description':'Native fixture hooks','hooks':{'SessionStart':[group('start','startup')],'PreToolUse':[group('deny' if args.scenario=='deny' else 'timeout' if args.scenario=='timeout' else 'pre','Bash'),group('excluded','apply_patch')],'PostToolUse':[group('post','Bash')]}}
 if args.scenario=='timeout':config['hooks']['PreToolUse'][0]['hooks'][0]['timeout']=1
 if args.scenario=='disabled-field':config['disableAllHooks']=True
 inline='[hooks]\n'+'\n'.join(json.dumps(k)+' = '+toml(v) for k,v in config['hooks'].items())+'\n'
 requests=[]
 class Handler(BaseHTTPRequestHandler):
  def log_message(self,*_):pass
  def do_POST(self):
   body=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))));requests.append(body);rid='fixture-response-'+str(len(requests))
   item={'type':'message','role':'assistant','id':'fixture-message','content':[{'type':'output_text','text':'Fixture complete.'}]}
   if len(requests)==1:item={'type':'function_call','call_id':'fixture-hook-command','name':'exec_command','arguments':json.dumps({'cmd':f'/usr/bin/python3 {probe}','workdir':str(workspace)})}
   events=[{'type':'response.created','response':{'id':rid}},{'type':'response.output_item.done','item':item},{'type':'response.completed','response':{'id':rid,'usage':{'input_tokens':0,'input_tokens_details':None,'output_tokens':0,'output_tokens_details':None,'total_tokens':0}}}]
   data=''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events).encode();self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
 server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
 native_config='model="fixture-model"\nmodel_provider="fixture"\napproval_policy="never"\nsandbox_mode="workspace-write"\n[features]\nenable_request_compression=false\nhooks=true\n[model_providers.fixture]\nname="Local fixture"\nbase_url="http://127.0.0.1:'+str(server.server_port)+'/v1"\nwire_api="responses"\nrequires_openai_auth=false\nsupports_websockets=false\n[projects.'+json.dumps(str(workspace))+']\ntrust_level="trusted"\n'
 (home/'config.toml').write_text(native_config);before={str(f.relative_to(home)):sha(f) for f in home.rglob('*') if f.is_file()}
 env={'PATH':'/usr/bin:/bin','HOME':str(home),'CODEX_HOME':str(home),'XDG_STATE_HOME':str(root/'state')}
 cli=root/'agents';subprocess.run(['go','build','-o',str(cli),'./cmd/agents'],cwd=repo/'CLI',check=True)
 def target(base):return base/('.codex' if args.scope=='project' else '')/('config.toml' if args.location=='inline' else 'hooks.json')
 def run_cli(operation,canonical,native_home=None):
  cmd=[str(cli),operation,'--vendor','codex','--root',str(canonical),'--scope',args.scope,'--experimental']
  if native_home:cmd+=['--native-home',str(native_home)]
  r=subprocess.run(cmd,env=env,capture_output=True,text=True);return {'command':cmd,'exit':r.returncode,'stdout':r.stdout,'stderr':r.stderr}
 result={'fixture':str(root),'scope':args.scope,'location':args.location,'scenario':args.scenario,'trust':args.trust,'direct':args.direct,'native_version':'0.154.0','native_sha256':sha(binary),'runner_sha256':sha(__file__),'helper_sha256':sha(Path(__file__).with_name('run_native_approvals.py')),'cli_sha256':sha(cli),'configuration':config,'external_model':False,'copied_credentials':False,'full_adapter_support':False,'implementation_sha256':{str(f.relative_to(repo)):sha(f) for f in sorted((repo/'CLI/internal/config').glob('native*.go'))}}
 client=None
 try:
  native_target=target(workspace if args.scope=='project' else home)
  if args.direct:
   native_target.parent.mkdir(parents=True,exist_ok=True)
   if args.location=='inline':native_target.write_text((native_config if args.scope=='user' else '')+inline)
   else:native_target.write_text(json.dumps(config))
  else:
   seed=target(source);seed.parent.mkdir(parents=True,exist_ok=True);seed.write_text(inline if args.location=='inline' else json.dumps(config))
   result['import']=run_cli('import',source,source if args.scope=='user' else None);assert result['import']['exit']==0,'import failed'
   shutil.copytree(source/'.agents',workspace/'.agents')
   result['apply']=run_cli('apply',workspace,home if args.scope=='user' else None);assert result['apply']['exit']==0,'apply failed'
  after={str(f.relative_to(home)):sha(f) for f in home.rglob('*') if f.is_file()};result.update(user_files_before_apply=before,user_files_after_apply=after)
  if args.scope=='project':assert before==after,'project apply changed user configuration'
  if args.location=='file':assert (home/'config.toml').read_text()==native_config,'hook projection changed native policy or trust'
  if args.location=='file':assert json.loads(native_target.read_text())==config,'hook configuration changed'
  command=[str(binary)]
  if args.trust=='bypass':command+=['--dangerously-bypass-hook-trust']
  command+=['app-server','--stdio'];result['native_command']=command
  client=Client(command,workspace,env,'deny',str(probe));deadline=time.monotonic()+40
  result['initialize']=client.response(client.request('initialize',{'clientInfo':{'name':'oda-hooks','version':'0.1.0'},'capabilities':{'experimentalApi':True}}),deadline);client.send({'method':'initialized','params':{}})
  result['hooks_list']=client.response(client.request('hooks/list',{'cwds':[str(workspace)]}),deadline)
  if args.trust=='preconfigured':
   definitions=[h for entry in result['hooks_list']['data'] for h in entry['hooks']]
   assert len(definitions)==4 and all(h['sourcePath']==str(native_target) and not h['isManaged'] for h in definitions),'unexpected native hooks before fixture trust'
   assert all(h['command'].startswith(shlex.join(['/usr/bin/python3',str(hook),str(log)])+' ') for h in definitions),'refuse to trust a non-fixture hook'
   client.close();result['inspection_events']=client.events
   with (home/'config.toml').open('a') as store:
    for definition in definitions:store.write('\n[hooks.state.'+json.dumps(definition['key'])+']\ntrusted_hash='+json.dumps(definition['currentHash'])+'\nenabled=true\n')
   trusted_hash=sha(home/'config.toml');result['fixture_native_trust_hash']=trusted_hash
   if not args.direct:
    result['reapply']=run_cli('apply',workspace,home if args.scope=='user' else None);assert result['reapply']['exit']==0,'apply after native trust failed'
    assert sha(home/'config.toml')==trusted_hash,'apply changed native hook trust'
   client=Client(command,workspace,env,'deny',str(probe));deadline=time.monotonic()+40
   client.response(client.request('initialize',{'clientInfo':{'name':'oda-hooks','version':'0.1.0'},'capabilities':{'experimentalApi':True}}),deadline);client.send({'method':'initialized','params':{}})
   result['trusted_hooks_list']=client.response(client.request('hooks/list',{'cwds':[str(workspace)]}),deadline)
   assert all(h['trustStatus']=='trusted' for entry in result['trusted_hooks_list']['data'] for h in entry['hooks']),'fixture hook trust was not loaded'

  thread=client.response(client.request('thread/start',{'cwd':str(workspace),'ephemeral':True}),deadline);result['thread']=thread
  result['turn']=client.response(client.request('turn/start',{'threadId':thread['thread']['id'],'input':[{'type':'text','text':'Run the isolated fixture command.'}]}),deadline)
  while True:
   event=client.receive(deadline)
   if event.get('method')=='turn/completed':result['completed_turn']=event['params']['turn'];break
  hooks=[json.loads(line) for line in log.read_text().splitlines()] if log.exists() else [];starts=[e for e in hooks if e['event']=='start'];result['hook_events']=hooks
  if args.trust=='unreviewed':assert not hooks,'unreviewed hooks executed'
  else:
   labels=[e['label'] for e in starts];assert 'start' in labels and 'excluded' not in labels,'hook discovery or matcher mismatch'
   assert all(e['cwd']==str(workspace) and e['input']['session_id']==thread['thread']['id'] for e in starts),'hook cwd or session mismatch'
   assert 'ODA_CODEX_HOOK_start' in json.dumps(requests[0]['input']),'session context missing'
   pre=next(e for e in starts if e['label'] in ['pre','deny','timeout']);assert pre['input']['tool_use_id']=='fixture-hook-command' and str(probe) in json.dumps(pre['input']['tool_input']),'pre-hook call correlation mismatch'
  if args.scenario=='deny' and args.trust!='unreviewed':
   assert not effect.exists(),'denied tool executed';assert 'ODA_CODEX_HOOK_DENIED' in json.dumps(requests[-1]['input']),'denial did not return to model'
  else:
   assert effect.read_text()=='ODA_CODEX_TOOL_EFFECT','tool effect absent'
   completed=[e['params']['item'] for e in client.events if e.get('method')=='item/completed' and e.get('params',{}).get('item',{}).get('type')=='commandExecution']
   assert any(e.get('status')=='completed' and str(probe) in e.get('command','') for e in completed),'no correlated native tool completion'
   if args.trust!='unreviewed':assert 'post' in labels and 'ODA_CODEX_HOOK_post' in json.dumps(requests[-1]['input']),'post-hook context absent'
  result['passed']=True
 except Exception as error:result.update(passed=False,error=str(error))
 finally:
  if client:client.close();result.update(events=client.events,approvals=client.approvals,stderr=client.errors)
  server.shutdown();server.server_close();result.update(model_requests=requests,effect=effect.read_text() if effect.exists() else '')
  if log.exists():result['hook_events']=[json.loads(line) for line in log.read_text().splitlines()]
  snapshot.write_bytes(Path(__file__).read_bytes());output.write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps({'output':str(output),'passed':result['passed'],'error':result.get('error')}));return 0 if result['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
