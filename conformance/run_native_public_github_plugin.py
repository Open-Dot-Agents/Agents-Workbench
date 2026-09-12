#!/usr/bin/env python3
"""Load the pinned GitHub plugin against its public MCP service without calls."""
import argparse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import traceback

from run_native_approvals import Client,PINS,sha, native_binary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();output=args.output.resolve();snapshot=output.with_suffix('.runner.py')
    assert not output.exists() and not snapshot.exists(),'refuse evidence replacement'
    binary=native_binary('codex');assert sha(binary)==PINS['codex']
    with snapshot.open('xb') as f:f.write(Path(__file__).read_bytes())
    token_process=subprocess.run(['gh','auth','token'],capture_output=True,text=True,timeout=15)
    token=token_process.stdout.strip();assert token_process.returncode==0 and token and '\n' not in token and '\r' not in token
    repo=Path(__file__).resolve().parents[2]
    source=repo/'.agents/plugins/com.openai.codex/plugins/github'
    provenance=json.loads((source.parents[1]/'provenance.json').read_text())
    assert all(sha(source/name)==digest for name,digest in provenance['files'].items())
    base=Path(tempfile.mkdtemp(prefix='agents-public-github-'))
    home,workspace,market=base/'home',base/'workspace',base/'market'
    for path in (home,workspace,market):path.mkdir(mode=0o700)
    subprocess.run(['git','init','-q',str(workspace)],check=True)
    package=market/'github';shutil.copytree(source,package)
    catalog={'name':'oda-public-github','owner':{'name':'Open-Dot-Agents fixture'},'plugins':[{'name':'github','source':'./github'}]}
    path=market/'.agents/plugins/marketplace.json';path.parent.mkdir(parents=True);path.write_text(json.dumps(catalog))
    requests=[];errors=[]
    class Model(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_POST(self):
            try:
                request=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append(request)
                identity='fixture-'+str(len(requests));item={'type':'message','role':'assistant','id':identity+'-message','content':[{'type':'output_text','text':'AGENTS_PUBLIC_GITHUB_READY'}]}
                events=[{'type':'response.created','response':{'id':identity}},{'type':'response.output_item.done','item':item},
                        {'type':'response.completed','response':{'id':identity,'usage':{'input_tokens':1,'output_tokens':1,'total_tokens':2}}}]
                data=''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events).encode()
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
            except Exception as error:errors.append(str(error))
    server=ThreadingHTTPServer(('127.0.0.1',0),Model);threading.Thread(target=server.serve_forever,daemon=True).start()
    config=('model="fixture-model"\nmodel_provider="fixture"\napproval_policy="never"\nsandbox_mode="read-only"\n'
            '[features]\nplugins=true\napps=false\nremote_plugin=false\nrecommended_plugins=false\nenable_request_compression=false\nrespect_system_proxy=false\n'
            '[analytics]\nenabled=false\n[model_providers.fixture]\nname="Fixture"\n'
            f'base_url="http://127.0.0.1:{server.server_port}/v1"\nwire_api="responses"\nrequires_openai_auth=false\nsupports_websockets=false\n'
            f'[projects.{json.dumps(str(workspace))}]\ntrust_level="trusted"\n')
    (home/'config.toml').write_text(config);(home/'config.toml').chmod(0o600)
    env={'PATH':'/usr/bin:/bin','HOME':str(home),'CODEX_HOME':str(home),'XDG_STATE_HOME':str(base/'state'),'GITHUB_PAT_TOKEN':token}
    result={'native_version':'0.154.0','native_sha256':sha(binary),'runner_sha256':sha(snapshot),
            'helper_sha256':sha(Path(__file__).with_name('run_native_approvals.py')),'fixture':str(base),
            'source_revision':provenance['revision'],'source_files':provenance['files'],'public_endpoint':'https://api.githubcopilot.com/mcp/',
            'credential_source':'gh auth keyring','credential_value_stored':False,'credential_sha256_stored':False,
            'remote_operation':'MCP initialization and tools/list through native client; model calls no GitHub tool',
            'external_model':False,'external_github_mcp':True,'remote_mutations':False,'full_adapter_support':False,'commands':[]}
    client=None
    try:
        for command in ([str(binary),'plugin','marketplace','add',str(market)],[str(binary),'plugin','add','github@oda-public-github']):
            process=subprocess.run(command,cwd=workspace,env=env,capture_output=True,text=True,timeout=45)
            row={'command':command,'exit_code':process.returncode,'stdout':process.stdout,'stderr':process.stderr};result['commands'].append(row)
            assert process.returncode==0,row
            assert token not in process.stdout and token not in process.stderr,'credential leaked in native output'
        selected=json.loads(subprocess.run([str(binary),'plugin','list','--json'],cwd=workspace,env=env,capture_output=True,text=True,check=True,timeout=30).stdout)
        result['plugin_list']=selected
        installed_config=(home/'config.toml').read_text()
        result['installed_config_sha256']=hashlib.sha256(installed_config.encode()).hexdigest()
        client=Client([str(binary),'app-server','--stdio'],workspace,env,'deny','')
        deadline=time.monotonic()+60
        client.response(client.request('initialize',{'clientInfo':{'name':'agents-public-github','version':'0'},'capabilities':{'experimentalApi':True}}),deadline);client.send({'method':'initialized','params':{}})
        started=client.response(client.request('thread/start',{'cwd':str(workspace),'ephemeral':True}),deadline);result['thread']=started
        startup=None
        while time.monotonic()<deadline:
            event=client.receive(deadline)
            params=event.get('params',{})
            if event.get('method')=='mcpServer/startupStatus/updated' and params.get('threadId')==started['thread']['id'] and params.get('name')=='github' and params.get('status') in ('ready','failed'):
                startup=params;break
        result['terminal_mcp_startup']=startup
        assert startup and startup['status']=='ready',startup
        client.response(client.request('turn/start',{'threadId':started['thread']['id'],'input':[{'type':'text','text':'Reply that the read-only GitHub MCP discovery test is ready. Do not call tools.'}]}),deadline)
        while True:
            event=client.receive(deadline)
            if event.get('method')=='turn/completed' and event.get('params',{}).get('threadId')==started['thread']['id']:
                result['completion']=event;break
        result.update(events=client.events,approvals=client.approvals,stderr=''.join(client.errors),model_requests=requests,provider_errors=errors)
        ready=[e['params'] for e in client.events if e.get('method')=='mcpServer/startupStatus/updated' and e.get('params',{}).get('name')=='github' and e['params'].get('status')=='ready']
        github_namespaces=[tool for request in requests for tool in request.get('tools',[])
                           if tool.get('type')=='namespace' and tool.get('name')=='mcp__github']
        assert len(github_namespaces)==1,github_namespaces
        nested_tools=github_namespaces[0].get('tools',[])
        assert nested_tools and all(isinstance(tool.get('name'),str) and tool.get('name') for tool in nested_tools)
        # Codex converts MCP inputSchema objects to Responses API function
        # parameters before it sends the namespace to the model provider.
        assert all(tool.get('type')=='function' and isinstance(tool.get('parameters'),dict) for tool in nested_tools)
        github_tools=sorted(tool['name'] for tool in nested_tools)
        assert len(github_tools)==len(set(github_tools))
        direct=json.loads((repo/'WORKBENCH/evidence/native-draft2-debug/public-github-mcp-readonly-probe.json').read_text())
        direct_tools=sorted(direct['tool_names'])
        result.update(mcp_ready_events=ready,github_namespace_count=len(github_namespaces),
                      github_tool_count=len(github_tools),github_tool_names=github_tools,
                      direct_tool_count=len(direct_tools),direct_tool_names_match=github_tools==direct_tools)
        assert ready and all(e['threadId']==started['thread']['id'] for e in ready)
        assert len(github_tools)==47 and 'get_me' in github_tools
        assert github_tools==direct_tools
        assert not any(e.get('method')=='item/completed' and e.get('params',{}).get('item',{}).get('type')=='mcpToolCall' for e in client.events)
        assert not client.approvals and result['completion']['params']['turn']['status']=='completed'
        assert any('AGENTS_PUBLIC_GITHUB_READY' in json.dumps(e) and started['thread']['id'] in json.dumps(e) for e in client.events)
        assert not errors and (home/'config.toml').read_text()==installed_config
        result['passed']=True
    except Exception as error:result.update(passed=False,error=type(error).__name__+': '+str(error),traceback=traceback.format_exc())
    finally:
        if client:
            client.close();result.setdefault('events',client.events);result.setdefault('approvals',client.approvals);result.setdefault('stderr',''.join(client.errors))
        server.shutdown();server.server_close()
        serialized=json.dumps(result,indent=2)
        if token in serialized:
            result={'passed':False,'error':'credential appeared in evidence payload','credential_value_stored':False,
                    'credential_sha256_stored':False,'remote_mutations':False}
            serialized=json.dumps(result,indent=2)
        token=''
        with output.open('x') as f:f.write(serialized+'\n')
    print(json.dumps({'passed':result['passed'],'github_tool_count':result.get('github_tool_count'),'ready_events':len(result.get('mcp_ready_events',[])),'error':result.get('error')}))
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
