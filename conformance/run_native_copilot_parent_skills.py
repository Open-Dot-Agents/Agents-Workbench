#!/usr/bin/env python3
"""Measure inherited skill import, relocation, override, and Git boundaries."""
import argparse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import traceback
import uuid

from run_native_approvals import Client,PINS,sha, native_binary
from verify_copilot_skill_import import phase_check


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();output=args.output.resolve();snapshot=output.with_suffix('.runner.py')
    assert not output.exists() and not snapshot.exists(),'refuse evidence replacement'
    binary=native_binary('copilot')
    assert sha(binary)==PINS['copilot']
    with snapshot.open('xb') as f:f.write(Path(__file__).read_bytes())
    repo=Path(__file__).resolve().parents[2]
    base=Path(tempfile.mkdtemp(prefix='agents-parent-skills-'))
    home,source,target=base/'home',base/'source',base/'target'
    source_child,target_child=source/'packages/child',target/'packages/child'
    for p in (home/'.copilot',source_child,target_child):p.mkdir(parents=True,mode=0o700)
    for p in (source,target):subprocess.run(['git','init','-q',str(p)],check=True)
    state=home/'.copilot/config.json'
    state.write_text(json.dumps({'trustedFolders':[str(source_child),str(target_child)],'firstLaunchAt':1234}));state.chmod(0o600)
    (home/'.copilot/settings.json').write_text('{"memory":false}')
    native_package=source/'.github/skills/fixture-import';(native_package/'scripts').mkdir(parents=True)
    (native_package/'SKILL.md').write_text('---\nname: fixture-import\ndescription: Inherited fixture.\n---\nAGENTS_SKILL_IMPORT_BODY\nUse only the isolated package script.\n')
    (native_package/'scripts/probe.py').write_text('from pathlib import Path\nPath("effect.txt").write_text((Path(__file__).resolve().parents[1]/"data.txt").read_text())\n')
    (native_package/'scripts/probe.py').chmod(0o755)
    (native_package/'data.txt').write_text('AGENTS_SKILL_IMPORT_ASSET')
    (native_package/'blob.bin').write_bytes(b'\x00\xff\x80parent\n')
    (source_child/'.github').mkdir();(source_child/'.github/copilot-instructions.md').write_text('Child fixture instructions.\n')
    env={'HOME':str(home),'COPILOT_HOME':str(home/'.copilot'),'COPILOT_CACHE_HOME':str(base/'cache'),
         'XDG_STATE_HOME':str(base/'state'),'PATH':'/usr/bin:/bin','COPILOT_OFFLINE':'true',
         'COPILOT_PROVIDER_TYPE':'openai','COPILOT_PROVIDER_WIRE_API':'completions','COPILOT_MODEL':'gpt-5.4'}
    cli=base/'agents';requests=[];errors=[];offset=0;active_probe=None
    result={'native_version':'1.0.83','native_sha256':sha(binary),'runner_sha256':sha(snapshot),'fixture':str(base),
            'helper_sha256':{name:sha(Path(__file__).with_name(name)) for name in ('run_native_approvals.py','verify_copilot_skill_import.py')},
            'implementation_sha256':{str(p.relative_to(repo)):sha(p) for directory in ('CLI/internal/config','CLI/cmd/agents') for p in (repo/directory).glob('*.go')},
            'commands':[],'phases':[],'full_adapter_support':False}

    class Model(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_POST(self):
            try:
                request=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append(request)
                number=len(requests)-offset;body_loaded='AGENTS_SKILL_IMPORT_BODY' in json.dumps(request['messages'])
                name=arguments=None
                if number==1:name,arguments='skill',{'skill':'fixture-import'}
                elif number==2 and body_loaded:name,arguments='bash',{'command':f'/usr/bin/python3 {active_probe}','description':'Read the inherited skill asset'}
                delta={'role':'assistant','content':'AGENTS_PARENT_SKILL_DONE'};finish='stop'
                if name:
                    delta={'role':'assistant','tool_calls':[{'index':0,'id':f'import-call-{number}','type':'function','function':{'name':name,'arguments':json.dumps(arguments)}}]};finish='tool_calls'
                chunks=[{'id':f'fixture-{number}','object':'chat.completion.chunk','created':1,'model':request['model'],'choices':[{'index':0,'delta':delta,'finish_reason':None}]},
                        {'id':f'fixture-{number}','object':'chat.completion.chunk','created':1,'model':request['model'],'choices':[{'index':0,'delta':{},'finish_reason':finish}],'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}]
                data=(''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n').encode()
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
            except Exception as error:errors.append(str(error))

    server=ThreadingHTTPServer(('127.0.0.1',0),Model)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    env['COPILOT_PROVIDER_BASE_URL']=f'http://127.0.0.1:{server.server_port}/v1';result['environment']=env

    def command(argv,cwd=source):
        before=sha(state)
        p=subprocess.run([str(x) for x in argv],cwd=cwd,env=None if argv[0]=='go' else env,capture_output=True,text=True,timeout=90)
        row={'command':[str(x) for x in argv],'exit_code':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
        result['commands'].append(row)
        if str(argv[0])==str(cli):
            row.update(authority_before=before,authority_after=sha(state));assert row['authority_before']==row['authority_after'],'adapter changed native state'
        assert p.returncode==0,json.dumps(row)
        return row
    def adapter(operation,root):return command([cli,operation,'--vendor','copilot','--root',root,'--experimental','--format','json']) if operation!='import' else command([cli,'import','--vendor','copilot','--root',root,'--experimental'])
    def hashes(root):return {str(p.relative_to(root)):sha(p) for p in sorted(root.rglob('*')) if p.is_file()}
    def metadata(root):return {str(p.relative_to(root)):{'mode':p.stat().st_mode & 0o777,'inode':p.stat().st_ino,'mtime_ns':p.stat().st_mtime_ns} for p in sorted(root.rglob('*')) if p.is_file()}
    def native(label,workspace,package,present=True,origin='inherited',effect='AGENTS_SKILL_IMPORT_ASSET'):
        nonlocal offset,active_probe
        offset=len(requests);active_probe=package/'scripts/probe.py'
        marker=workspace/'effect.txt'
        if marker.exists():marker.unlink()
        phase={'label':label,'workspace':str(workspace),'package':str(package),'nonce':'AGENTS_PARENT_'+uuid.uuid4().hex}
        result['phases'].append(phase)
        phase['discovery']=json.loads(command([binary,'skill','list','--json'],cwd=workspace)['stdout'])
        client=Client([str(binary),'--acp','--disable-builtin-mcps','--no-auto-update','--no-remote'],workspace,env,'allow',str(active_probe))
        try:
            deadline=time.monotonic()+50
            client.response(client.request('initialize',{'protocolVersion':1,'clientCapabilities':{}}),deadline)
            phase['session']=client.response(client.request('session/new',{'cwd':str(workspace),'mcpServers':[]}),deadline)
            phase['prompt']=client.response(client.request('session/prompt',{'sessionId':phase['session']['sessionId'],'prompt':[{'type':'text','text':phase['nonce']}]}),deadline)
        finally:
            client.close();phase.update(events=client.events,approvals=client.approvals,stderr=client.errors,requests=requests[offset:])
            phase['effect']=marker.read_text() if marker.exists() else None
            phase['body_loaded']=any('AGENTS_SKILL_IMPORT_BODY' in json.dumps(q['messages']) for q in phase['requests'])
            if 'session' in phase:
                events=home/'.copilot/session-state'/phase['session']['sessionId']/'events.jsonl'
                phase['native_events']=[json.loads(line) for line in events.read_text().splitlines()] if events.exists() else []
        phase_check(phase,present=present,source=origin,effect=effect)
        assert any('AGENTS_PARENT_SKILL_DONE' in json.dumps(e) and phase['session']['sessionId'] in json.dumps(e) for e in phase['events']),'native response absent'
        assert not errors,errors

    try:
        command(['go','build','-o',cli,'./cmd/agents'],cwd=repo/'CLI')
        result['original_hashes']=hashes(native_package)
        result['original_metadata']=metadata(native_package)
        native('source',source_child,native_package)
        adapter('import',source)
        result['imported_hashes']=hashes(source/'.agents/skills/fixture-import')
        parent_before=hashes(source/'.agents');native_before=hashes(native_package)
        adapter('import',source_child)
        result['child_did_not_capture_parent']=not (source_child/'.agents/skills/fixture-import').exists()
        result['child_import_preserved_parent']=parent_before==hashes(source/'.agents') and native_before==hashes(native_package)
        assert result['child_did_not_capture_parent'] and result['child_import_preserved_parent']
        shutil.copytree(source/'.agents',target/'.agents')
        package=target/'.agents/skills/fixture-import'
        result['relocated_hashes']=hashes(package)
        result['parent_plan']=json.loads(adapter('apply',target)['stdout'])
        native('relocated',target_child,package)
        (package/'data.txt').write_text('AGENTS_PARENT_UPDATED')
        adapter('apply',target)
        native('updated',target_child,package,effect='AGENTS_PARENT_UPDATED')
        child_core=target_child/'.agents';child_core.mkdir()
        (child_core/'AGENTS.md').write_text('Local child instructions.\n')
        (child_core/'manifest.json').write_text('{"version":"1.1.0-draft.2","profiles":["native","skills"]}')
        directory=child_core/'native/com.github.copilot';directory.mkdir(parents=True)
        (directory/'profile.json').write_text('{"namespace":"com.github.copilot","harness_version":"=1.0.83","scope":"project","required":true,"artifacts":[]}')
        local=child_core/'skills/fixture-import';shutil.copytree(package,local);(local/'data.txt').write_text('AGENTS_CHILD_OVERRIDE')
        parent_before=hashes(target/'.agents')
        result['child_plan']=json.loads(adapter('apply',target_child)['stdout'])
        assert parent_before==hashes(target/'.agents'),'child apply changed parent'
        native('override',target_child,local,origin='project',effect='AGENTS_CHILD_OVERRIDE')
        shutil.rmtree(child_core/'skills')
        (child_core/'manifest.json').write_text('{"version":"1.1.0-draft.2","profiles":["native"]}')
        adapter('apply',target_child)
        assert parent_before==hashes(target/'.agents'),'child removal changed parent'
        native('fallback',target_child,package,effect='AGENTS_PARENT_UPDATED')
        subprocess.run(['git','init','-q',str(target_child)],check=True)
        adapter('apply',target_child)
        native('nested-git',target_child,package,present=False)
        result['parent_unchanged_by_child']=parent_before==hashes(target/'.agents')
        result['source_unchanged']=result['original_hashes']==hashes(native_package)
        result['source_metadata_after']=metadata(native_package)
        assert result['original_metadata']==result['source_metadata_after'],'source permissions or file identity changed'
        assert result['source_unchanged'] and result['parent_unchanged_by_child']
        assert result['original_hashes']==result['imported_hashes']==result['relocated_hashes']
        result['passed']=True
    except Exception as error:result.update(passed=False,error=str(error),traceback=traceback.format_exc())
    finally:
        server.shutdown();server.server_close();result['provider_errors']=errors
        with output.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    print(json.dumps({'passed':result['passed'],'output':str(output),'error':result.get('error')}))
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
