#!/usr/bin/env python3
"""Test unattended, composed, and background commands in trust-derived mode."""
import argparse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import shlex
import signal
import subprocess
import tempfile
import threading
import time
import traceback

from run_native_approvals import Client,PINS,sha, native_binary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case',choices=['unattended','composed-deny','background-deny','pending-timeout','disconnect','post-start-disconnect'],required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();output=args.output.resolve();snapshot=output.with_suffix('.runner.py')
    assert not output.exists() and not snapshot.exists(),'refuse evidence replacement'
    binary=native_binary('codex');assert sha(binary)==PINS['codex']
    with snapshot.open('xb') as f:f.write(Path(__file__).read_bytes())
    base=Path(tempfile.mkdtemp(prefix='agents-trust-boundary-'))
    home,workspace=base/'home',base/'workspace';home.mkdir(mode=0o700);workspace.mkdir(mode=0o700)
    subprocess.run(['git','-c','init.templateDir=','init','-q',str(workspace)],check=True,env={'PATH':'/usr/bin:/bin','GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_NOSYSTEM':'1'})
    first,second=workspace/'first.py',workspace/'second.py'
    first_marker,second_marker=workspace/'first.effect',workspace/'second.effect'
    if args.case=='post-start-disconnect':
        first.write_text('from pathlib import Path\nimport os,subprocess\nsubprocess.Popen(["/usr/bin/python3", '+repr(str(second))+'], start_new_session=True)\nPath('+repr(str(first_marker))+').write_text(str(os.getpid()))\n')
        second.write_text('from pathlib import Path\nimport time\ntime.sleep(1.5)\nPath('+repr(str(second_marker))+').write_text("SECOND")\n')
    else:
        first.write_text('from pathlib import Path\nimport os,time\nPath('+repr(str(first_marker))+').write_text(str(os.getpid()))\ntime.sleep(.3)\n')
        second.write_text('from pathlib import Path\nPath('+repr(str(second_marker))+').write_text("SECOND")\n')
    command=f'/usr/bin/python3 {first}'
    if args.case=='composed-deny':command+=f' && /usr/bin/python3 {second}'
    if args.case=='background-deny':command+=f' & /usr/bin/python3 {second}'
    requests=[];responses=[];step=0
    class Model(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_POST(self):
            nonlocal step
            raw=self.rfile.read(int(self.headers.get('Content-Length',0)));request=json.loads(raw);requests.append(request)
            identity='fixture-'+str(len(requests))
            item={'type':'message','role':'assistant','id':identity+'-message','content':[{'type':'output_text','text':'AGENTS_TRUST_BOUNDARY_DONE'}]}
            if step==0:
                step=1;item={'type':'function_call','call_id':'trust-boundary-call','name':'exec_command','arguments':json.dumps({'cmd':command,'workdir':str(workspace)})}
            events=[{'type':'response.created','response':{'id':identity}},{'type':'response.output_item.done','item':item},
                    {'type':'response.completed','response':{'id':identity,'usage':{'input_tokens':1,'output_tokens':1,'total_tokens':2}}}]
            responses.append(events);data=''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events).encode()
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    server=ThreadingHTTPServer(('127.0.0.1',0),Model);threading.Thread(target=server.serve_forever,daemon=True).start()
    config=('model="fixture-model"\nmodel_provider="fixture"\napprovals_reviewer="user"\n'
            '[features]\nenable_request_compression=false\nremote_plugin=false\nrecommended_plugins=false\napps=false\nrespect_system_proxy=false\n'
            '[analytics]\nenabled=false\n[model_providers.fixture]\nname="Fixture"\n'
            f'base_url="http://127.0.0.1:{server.server_port}/v1"\nwire_api="responses"\nrequires_openai_auth=false\nsupports_websockets=false\n'
            f'[projects.{json.dumps(str(workspace))}]\ntrust_level="untrusted"\n')
    (home/'config.toml').write_text(config);(workspace/'.codex').mkdir();(workspace/'.codex/config.toml').write_text('approval_policy="never"\nmodel="ODA_DISABLED_PROJECT_MODEL"\n')
    env={'PATH':'/usr/bin:/bin','HOME':str(home),'CODEX_HOME':str(home),'XDG_STATE_HOME':str(base/'state')}
    result={'case':args.case,'fixture':str(base),'native_version':'0.154.0','native_sha256':sha(binary),'runner_sha256':sha(snapshot),
            'helper_sha256':sha(Path(__file__).with_name('run_native_approvals.py')),'command_under_test':command,'config':config,
            'approval_policy':'omitted; trust-derived untrusted','project_configuration_sentinel':'ODA_DISABLED_PROJECT_MODEL',
            'full_adapter_support':False,'portable_security_projection_tested':False,'environment':env}

    class PendingClient(Client):
        def __init__(self,*values):
            super().__init__(*values);self.pending=None
        def receive(self,deadline):
            while time.monotonic()<deadline:
                try:kind,line=self.incoming.get(timeout=min(1,max(.01,deadline-time.monotonic())))
                except queue.Empty:
                    if self.process.poll() is not None:raise RuntimeError('native server exited')
                    continue
                if line is None:
                    if kind=='stdout':raise RuntimeError('native server closed stdout')
                    continue
                if kind=='stderr':self.errors.append(line);continue
                try:event=json.loads(line)
                except ValueError:self.errors.append(line);continue
                self.events.append(event)
                if event.get('method')=='item/commandExecution/requestApproval' and 'id' in event:
                    self.pending=event
                    self.approvals.append({'method':event['method'],'params':event.get('params',{}),'response':None,'approved':False})
                elif 'method' in event and 'id' in event:
                    self.send({'id':event['id'],'error':{'code':-32601,'message':'Fixture request unsupported'}})
                return event
            raise TimeoutError('native case deadline reached')
    client=None
    try:
        deadline=time.monotonic()+45
        if args.case=='unattended':
            preflight=Client([str(binary),'app-server','--stdio'],workspace,env,'deny',str(first))
            try:
                preflight.response(preflight.request('initialize',{'clientInfo':{'name':'agents-trust-preflight','version':'0'}}),deadline);preflight.send({'method':'initialized','params':{}})
                result['effective_thread']=preflight.response(preflight.request('thread/start',{'cwd':str(workspace),'ephemeral':True}),deadline)
            finally:
                preflight.close();result['preflight_events']=preflight.events;result['preflight_stderr']=''.join(preflight.errors)
            assert result['effective_thread']['approvalPolicy']=='untrusted' and result['effective_thread']['model']=='fixture-model'
            native=[str(binary),'exec','--ephemeral','--json','-C',str(workspace),'Run the isolated fixture command exactly once. Stop if permission cannot be requested.']
            process=subprocess.run(native,cwd=workspace,env=env,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=45)
            result.update(native_command=native,native_exit_code=process.returncode,stdout=process.stdout,stderr=process.stderr,
                          native_events=[json.loads(line) for line in process.stdout.splitlines() if line.startswith('{')])
        elif args.case=='post-start-disconnect':
            native=[str(binary),'app-server','--stdio'];result['native_command']=native
            client=Client(native,workspace,env,'allow',str(first))
            client.response(client.request('initialize',{'clientInfo':{'name':'agents-trust-post-start','version':'0'}}),deadline);client.send({'method':'initialized','params':{}})
            started=client.response(client.request('thread/start',{'cwd':str(workspace),'ephemeral':True}),deadline);result['thread']=started
            client.response(client.request('turn/start',{'threadId':started['thread']['id'],'input':[{'type':'text','text':'Run the isolated fixture command exactly once.'}]}),deadline)
            while not first_marker.exists():client.receive(deadline)
            result['first_effect_before_disconnect']=first_marker.read_text()
            result['second_effect_before_disconnect']=second_marker.read_text() if second_marker.exists() else None
            result['command_completed_before_disconnect']=any(e.get('method')=='item/completed' and
                e.get('params',{}).get('item',{}).get('id')=='trust-boundary-call' and
                e.get('params',{}).get('item',{}).get('status')=='completed' for e in client.events)
            os.killpg(client.process.pid,signal.SIGTERM);client.process.wait(timeout=5)
            result['native_exit_after_disconnect']=client.process.returncode
            time.sleep(2.3)
            result.update(events=client.events,approvals=client.approvals,stderr=''.join(client.errors))
        elif args.case in ('composed-deny','background-deny'):
            native=[str(binary),'app-server','--stdio'];result['native_command']=native
            # The helper approves only one exact simple fixture command. These
            # composed commands therefore receive an explicit client denial.
            client=Client(native,workspace,env,'allow',str(first))
            client.response(client.request('initialize',{'clientInfo':{'name':'agents-trust-boundary','version':'0'}}),deadline);client.send({'method':'initialized','params':{}})
            started=client.response(client.request('thread/start',{'cwd':str(workspace),'ephemeral':True}),deadline);result['thread']=started
            client.response(client.request('turn/start',{'threadId':started['thread']['id'],'input':[{'type':'text','text':'Run the isolated fixture command exactly once. Stop on denial.'}]}),deadline)
            while True:
                event=client.receive(deadline)
                if event.get('method')=='turn/completed' and event.get('params',{}).get('threadId')==started['thread']['id']:
                    result['completion']=event;break
            result.update(events=client.events,approvals=client.approvals,stderr=''.join(client.errors))
        else:
            native=[str(binary),'app-server','--stdio'];result['native_command']=native
            client=PendingClient(native,workspace,env,'deny',str(first))
            client.response(client.request('initialize',{'clientInfo':{'name':'agents-trust-pending','version':'0'}}),deadline);client.send({'method':'initialized','params':{}})
            started=client.response(client.request('thread/start',{'cwd':str(workspace),'ephemeral':True}),deadline);result['thread']=started
            client.response(client.request('turn/start',{'threadId':started['thread']['id'],'input':[{'type':'text','text':'Run the isolated fixture command exactly once.'}]}),deadline)
            while client.pending is None:client.receive(deadline)
            result['pending_request']=client.pending
            observed_at=time.monotonic();window=5.0
            while time.monotonic()-observed_at<window:
                if client.process.poll() is not None:break
                time.sleep(.05)
            result.update(observation_seconds=time.monotonic()-observed_at,native_alive_after_window=client.process.poll() is None,
                          first_effect_after_window=first_marker.read_text() if first_marker.exists() else None,
                          second_effect_after_window=second_marker.read_text() if second_marker.exists() else None)
            if args.case=='pending-timeout':
                assert result['native_alive_after_window'] and result['first_effect_after_window'] is None and result['second_effect_after_window'] is None
                # End the fixture with an explicit denial after the observation.
                client.send({'id':client.pending['id'],'result':{'decision':'decline'}})
                client.approvals[0]['response']={'decision':'decline'}
                while True:
                    event=client.receive(deadline)
                    if event.get('method')=='turn/completed' and event.get('params',{}).get('threadId')==started['thread']['id']:
                        result['completion']=event;break
                result['outcome']='no-native-approval-timeout-in-observation-window'
            else:
                os.killpg(client.process.pid,signal.SIGTERM);client.process.wait(timeout=5)
                time.sleep(.8)
                result['native_exit_after_disconnect']=client.process.returncode
                result['outcome']='disconnect-before-approval-response-prevents-execution'
            result.update(events=client.events,approvals=client.approvals,stderr=''.join(client.errors))
        time.sleep(.8)
        result.update(first_effect=first_marker.read_text() if first_marker.exists() else None,second_effect=second_marker.read_text() if second_marker.exists() else None,
                      requests=requests,responses=responses,config_unchanged=(home/'config.toml').read_text()==config)
        assert result['config_unchanged'] and requests
        assert all(r['model']=='fixture-model' for r in requests)
        assert all('ODA_DISABLED_PROJECT_MODEL' not in json.dumps(r) for r in requests)
        if args.case=='unattended':
            commands=[e['item'] for e in result['native_events'] if e.get('type')=='item.completed' and e.get('item',{}).get('type')=='command_execution' and str(first) in e['item'].get('command','')]
            assert len(commands)==1 and commands[0]['status']=='completed' and commands[0]['exit_code']==0
            assert result['first_effect'] and result['second_effect'] is None
            assert int(result['first_effect'])>1
            result['outcome']='native-unattended-approval-bypass'
        elif args.case=='post-start-disconnect':
            assert result['first_effect'] and result['second_effect'] is None
            assert result['second_effect_before_disconnect'] is None
            assert result['command_completed_before_disconnect']
            relevant=[a for a in result['approvals'] if a['params'].get('itemId')=='trust-boundary-call']
            assert len(relevant)==1 and relevant[0]['approved'] and relevant[0]['response']['decision']=='accept'
            assert result['native_exit_after_disconnect'] is not None
            result['outcome']='detached-background-descendant-terminated-at-command-completion'
        elif args.case in ('composed-deny','background-deny'):
            assert result['first_effect'] is None and result['second_effect'] is None
            relevant=[a for a in result['approvals'] if a['params'].get('itemId')=='trust-boundary-call']
            assert len(relevant)==1 and not relevant[0]['approved'] and relevant[0]['response']['decision']=='decline'
            terminal=[e['params']['item'] for e in result['events'] if e.get('method')=='item/completed' and e.get('params',{}).get('item',{}).get('id')=='trust-boundary-call']
            assert len(terminal)==1 and terminal[0]['status']=='declined'
            assert result['completion']['params']['turn']['status']=='completed'
            result['outcome']='verified-composed-denial-before-descendants'
        else:
            assert result['first_effect'] is None and result['second_effect'] is None
            assert result['pending_request']['method']=='item/commandExecution/requestApproval'
            assert result['pending_request']['params']['itemId']=='trust-boundary-call'
            assert result['observation_seconds']>=4.9
            if args.case=='pending-timeout':
                terminal=[e['params']['item'] for e in result['events'] if e.get('method')=='item/completed' and e.get('params',{}).get('item',{}).get('id')=='trust-boundary-call']
                assert len(terminal)==1 and terminal[0]['status']=='declined'
                assert result['completion']['params']['turn']['status']=='completed'
            else:
                assert result['native_exit_after_disconnect'] is not None
        result['passed']=True
    except Exception as error:result.update(passed=False,error=str(error),traceback=traceback.format_exc())
    finally:
        if client:client.close()
        server.shutdown();server.server_close()
        result.setdefault('requests',requests);result.setdefault('responses',responses)
        with output.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    print(json.dumps({'case':args.case,'passed':result['passed'],'outcome':result.get('outcome'),'error':result.get('error')}))
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
