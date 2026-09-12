#!/usr/bin/env python3
"""Measure native keymap overrides, chords, and explicit unbinding."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time
import tomllib
import traceback

import pexpect

from run_native_approvals import PINS, sha, native_binary
from run_native_copilot_preferences import plain_terminal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope', choices=['project', 'user'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve(); snapshot = output.with_suffix('.runner.py')
    assert not output.exists() and not snapshot.exists(), 'refuse evidence replacement'
    binary = native_binary('codex')
    assert sha(binary) == PINS['codex']
    with snapshot.open('xb') as stream: stream.write(Path(__file__).read_bytes())
    repo = Path(__file__).resolve().parents[2]
    base = Path(tempfile.mkdtemp(prefix='agents-keymap-'))
    home, workspace = base/'home', base/'workspace'
    native = home/'.codex'; native.mkdir(parents=True, mode=0o700); workspace.mkdir(mode=0o700)
    subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
    config = native/'config.toml'
    config.write_text('model="fixture-model"\nmodel_provider="fixture"\ncheck_for_update_on_startup=false\n[analytics]\nenabled=false\n[features]\nremote_plugin=false\nrecommended_plugins=false\napps=false\nrespect_system_proxy=false\n'
                      '[model_providers.fixture]\nname="Fixture"\nbase_url="http://127.0.0.1:9/v1"\nwire_api="responses"\nrequires_openai_auth=false\nsupports_websockets=false\n'
                      f'[projects.{json.dumps(str(workspace))}]\ntrust_level="trusted"\n')
    config.chmod(0o600)
    effect, editor = base/'effect.json', base/'editor.py'
    editor.write_text('import os,sys,time,json\nfrom pathlib import Path\n'
                      f'Path({str(effect)!r}).write_text(json.dumps({{"pid":os.getpid(),"parent":os.getppid(),"cwd":os.getcwd(),"time":time.monotonic(),"file":sys.argv[-1]}}))\n'
                      'Path(sys.argv[-1]).write_text("AGENTS_KEYMAP_EDITED")\n')
    env = {'HOME': str(home), 'CODEX_HOME': str(native), 'XDG_STATE_HOME': str(base/'state'),
           'PATH': '/usr/bin:/bin', 'TERM': 'xterm-256color', 'VISUAL': f'/usr/bin/python3 {editor}', 'EDITOR': f'/usr/bin/python3 {editor}'}
    result = {'scope': args.scope, 'fixture': str(base), 'native_version': '0.154.0', 'native_sha256': sha(binary),
              'runner_sha256': sha(snapshot), 'helper_sha256': {name:sha(Path(__file__).with_name(name)) for name in ('run_native_approvals.py','run_native_copilot_preferences.py')},
              'implementation_sha256': {str(p.relative_to(repo)):sha(p) for directory in ('CLI/internal/config','CLI/cmd/agents') for p in (repo/directory).glob('*.go')},
              'environment': env, 'editor_source': editor.read_text(), 'commands': [], 'phases': [], 'full_adapter_support': False,
              'limitations': ['Only the external editor action is exercised. Other action identities use the pinned schema.',
                              'This probe does not change native approval or sandbox policy.']}
    cli = base/'agents'

    def authority():
        value = tomllib.loads(config.read_text()); value.pop('tui',None); return value

    def invoke(argv,cwd=workspace):
        before = authority()
        proc = subprocess.run([str(x) for x in argv],cwd=cwd,env=None if argv[0]=='go' else env,capture_output=True,text=True,timeout=90)
        row={'command':[str(x) for x in argv],'exit_code':proc.returncode,'stdout':proc.stdout,'stderr':proc.stderr,'authority_before':before,'authority_after':authority()}
        result['commands'].append(row)
        assert proc.returncode==0 and row['authority_before']==row['authority_after'],json.dumps(row)
        return row

    def adapter(operation,root):
        argv=[cli,operation,'--root',root,'--vendor','codex','--experimental','--scope',args.scope]
        if args.scope=='user':argv.extend(['--native-home',native])
        return invoke(argv)

    def terminal(phase):
        child=pexpect.spawn(str(binary),['--no-alt-screen'],cwd=str(workspace),env=env,encoding='utf-8',dimensions=(40,140),timeout=1)
        phase.update(pid=child.pid,command=[str(binary),'--no-alt-screen'],keys=[])
        raw=''
        pending=''
        def pump(seconds):
            nonlocal raw,pending
            end=time.monotonic()+seconds
            while time.monotonic()<end and child.isalive():
                try:chunk=child.read_nonblocking(32768,timeout=.2)
                except pexpect.TIMEOUT:continue
                except pexpect.EOF:break
                raw+=chunk;pending+=chunk
                for query,reply in [('\x1b[6n','\x1b[1;1R'),('\x1b]11;?','\x1b]11;rgb:0000/0000/0000\x1b\\')]:
                    if query in pending:child.send(reply);pending=pending.replace(query,'')
                pending=pending[-16:]
        def press(keys,expected):
            if effect.exists():effect.unlink()
            start=time.monotonic();child.send(keys);pump(2)
            observed=json.loads(effect.read_text()) if effect.exists() else None
            phase['keys'].append({'input':keys,'start':start,'expected_effect':expected,'effect':observed})
            assert bool(observed)==expected,'editor effect differs from selected key binding'
            if observed:
                assert observed['parent']==child.pid and observed['cwd']==str(workspace) and observed['time']>=start,'editor effect lacks process/key correlation'
                assert 'AGENTS_KEYMAP_EDITED' in plain_terminal(raw),'native composer did not reload edited text'
        try:
            pump(7)
            assert child.isalive(),'native exited before input'
            press('\x07',phase['name']=='default') # The built-in editor key is replaced or explicitly unbound.
            if phase['name']=='default':press('\x1b[18~',False)
            elif phase['name']=='override':press('\x1b[18~',True)
            elif phase['name']=='chord':
                press('\x1b[18~',False)
                press('\x18\x05',True)
            else:
                press('\x1b[18~',False)
                press('\x18\x05',False)
            phase['alive_before_teardown']=child.isalive()
        finally:
            phase.update(raw_output=raw,output=plain_terminal(raw))
            try:child.close(force=True)
            except pexpect.ExceptionPexpect:
                end=time.monotonic()+5
                while child.isalive() and time.monotonic()<end:time.sleep(.05)
                child.close(force=True)
            phase.update(alive_after_teardown=child.isalive(),exit_status=child.exitstatus,signal_status=child.signalstatus)

    try:
        invoke(['go','build','-o',cli,'./cmd/agents'],cwd=repo/'CLI')
        baseline={'name':'default'};result['phases'].append(baseline)
        terminal(baseline)
        directory=workspace/'.agents/native/com.openai.codex';directory.mkdir(parents=True)
        (workspace/'.agents/AGENTS.md').write_text('Use the isolated fixture.\n')
        (workspace/'.agents/manifest.json').write_text('{"version":"1.1.0-draft.2","profiles":["native"]}')
        (directory/'profile.json').write_text(json.dumps({'namespace':'com.openai.codex','harness_version':'=0.154.0','scope':args.scope,'required':True,'artifacts':[{'kind':'config','source':'config.toml'}]}))
        target=(native if args.scope=='user' else workspace/'.codex')/'config.toml'
        for name,binding in [('override','"f7"'),('chord','"ctrl-x ctrl-e"'),('unbound','[]')]:
            source='[tui.keymap.global]\nopen_external_editor='+binding+'\n'
            (directory/'config.toml').write_text(source)
            expected=tomllib.loads(source)['tui']['keymap']
            phase={'name':name,'expected':expected};result['phases'].append(phase)
            before=effect.read_bytes() if effect.exists() else None
            adapter('apply',workspace)
            assert (effect.read_bytes() if effect.exists() else None)==before,'apply executed the editor'
            phase['projected']=tomllib.loads(target.read_text())['tui']['keymap']
            assert phase['projected']==expected
            terminal(phase)
            target_hash=sha(target)
            imported=base/('reimport-'+name) if args.scope=='user' else workspace
            adapter('import',imported)
            phase['reimported']=tomllib.loads((imported/'.agents/native/com.openai.codex/config.toml').read_text())['tui']['keymap']
            assert phase['reimported']==expected and sha(target)==target_hash,'reimport changed key bindings or source'
        result['passed']=True
    except Exception as error:result.update(passed=False,error=str(error),traceback=traceback.format_exc())
    finally:
        with output.open('x') as stream:json.dump(result,stream,indent=2);stream.write('\n')
    print(json.dumps({'passed':result['passed'],'output':str(output),'error':result.get('error')}))
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
