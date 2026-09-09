#!/usr/bin/env python3
"""Run extended native cases; each record states its exact observations."""
from __future__ import annotations
import argparse
import contextlib
import hashlib
import json
import os
import shlex
import ssl
import sys
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import extended_server
import codex_terminal
import run_adapter as native

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE.parent / 'evidence/results/extended'
SNAPSHOTS = {p.name:p.read_bytes() for p in
             (Path(__file__), HERE/'extended_server.py', HERE/'hook_probe.py', HERE/'codex_terminal.py', HERE/'run_adapter.py')}
SOURCES = {name:hashlib.sha256(data).hexdigest() for name,data in SNAPSHOTS.items()}



def write(path: Path, value: str | dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2)+'\n' if isinstance(value, dict) else value)


def events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line] if path.exists() else []


class Case:
    def __init__(self, vendor: str, case_id: str, directory: Path, agents: str, executable: str, metadata: dict):
        self.vendor, self.case_id, self.directory = vendor, case_id, directory
        self.root, self.log = directory/'repository', directory/'server.jsonl'
        self.root.mkdir()
        self.helpers = directory/"helpers"
        self.helpers.mkdir()
        for name in ("extended_server.py", "hook_probe.py"):
            (self.helpers/name).write_bytes(SNAPSHOTS[name])
        self.helper_hashes = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.helpers.iterdir()}
        self.agents, self.executable = agents, executable
        self.metadata = dict(metadata)
        self.env = native.harness_environment(vendor, executable, self.metadata, directory)
        self.transcripts: list[dict] = []
        self.checks: list[dict] = []
        self.marker = 'probe-'+uuid.uuid4().hex
        write(self.root/'.agents/AGENTS.md', '# Native test\n\nWhen asked ODA_EXTENDED, call the native oda-marker record tool once with\nmarker `'+self.marker+'`. Do not execute MCP servers or write marker files.\nIf a native tool is unavailable, report it without a workaround.\n')
        (self.root/'AGENTS.md').symlink_to('.agents/AGENTS.md')
        write(self.root/'.agents/manifest.json', {'version':'1.0.0','profiles':['tools']})
        subprocess.run(['git','init','-q'], cwd=self.root, check=True)
        self.stdio()

    def stdio(self, expected_env: str = '', args: list[str] | None = None):
        server = {'type':'stdio','command':'python3','args':[str(self.helpers/'extended_server.py'),str(self.log),expected_env,*(args or [])]}
        if expected_env:
            server['env'] = {'ODA_NATIVE_ENV':'urn:open-dot-agents:env:ODA_NATIVE_ENV'}
        write(self.root/'.agents/tools/mcp.json', {'mcpServers':{'oda-marker':server}})

    def check(self, identifier: str, passed: bool, observation=None):
        self.checks.append({'id':identifier,'passed':bool(passed),'observation':observation})

    def apply(self, expect_refusal=False):
        before = {str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file() and '.git' not in p.parts}
        p = subprocess.run([self.agents,'apply','--vendor',self.vendor,'--root',str(self.root),'--format','json'],text=True,capture_output=True)
        self.transcripts.append({'kind':'apply','returncode':p.returncode,'output':p.stdout+p.stderr})
        if expect_refusal:
            after = {str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file() and '.git' not in p.parts}
            self.check('adapter-refuses-before-writes',p.returncode != 0 and before == after,p.stdout+p.stderr)
        elif p.returncode:
            raise RuntimeError('apply failed: '+p.stdout+p.stderr)
        return p

    def run(self, prompt='ODA_EXTENDED. Follow the applicable instructions.', cwd=None, command=None):
        cmd = command or native.command(self.vendor,self.executable,cwd or self.root,prompt)
        start = time.monotonic()
        try:
            p = subprocess.run(cmd,cwd=cwd or self.root,env=self.env,stdin=subprocess.DEVNULL,
                               text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=600)
        except subprocess.TimeoutExpired as error:
            output=error.stdout or ''
            if isinstance(output,bytes):output=output.decode(errors='replace')
            self.transcripts.append({'kind':'native','command':cmd,'returncode':None,
                                     'elapsedSeconds':round(time.monotonic()-start,3),'timedOut':True,
                                     'nativeMcpMarkers':native.native_mcp_markers(self.vendor,output),
                                     'output':native.bounded_transcript(output)})
            raise
        markers = native.native_mcp_markers(self.vendor,p.stdout)
        self.transcripts.append({'kind':'native','command':cmd,'returncode':p.returncode,
                                 'elapsedSeconds':round(time.monotonic()-start,3),
                                 'nativeMcpMarkers':markers,'output':native.bounded_transcript(p.stdout)})
        self.check('native-process',p.returncode == 0)
        return p, markers

    def call_observed(self, markers, marker=None):
        marker = marker or self.marker
        calls = [e for e in events(self.log) if e.get('event')=='tool-call' and e.get('marker')==marker]
        self.check('native-mcp-call',marker in markers and len(calls)==1,{'markers':markers,'serverCalls':calls})


@contextlib.contextmanager
def remote_peer(case: Case, authenticated: bool):
    cert,key=case.directory/'cert.pem',case.directory/'key.pem'
    ca,ca_key,csr=case.directory/'ca.pem',case.directory/'ca-key.pem',case.directory/'server.csr'
    commands=[['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=ODA Test CA',
               '-addext','basicConstraints=critical,CA:TRUE','-addext','keyUsage=critical,keyCertSign,cRLSign','-keyout',str(ca_key),'-out',str(ca)],
              ['openssl','req','-new','-newkey','rsa:2048','-nodes','-subj','/CN=localhost','-keyout',str(key),'-out',str(csr)]]
    extensions=case.directory/'server.ext'
    extensions.write_text('basicConstraints=critical,CA:FALSE\nsubjectAltName=DNS:localhost,IP:127.0.0.1\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n')
    commands.append(['openssl','x509','-req','-in',str(csr),'-CA',str(ca),'-CAkey',str(ca_key),'-CAcreateserial',
                     '-days','1','-extfile',str(extensions),'-out',str(cert)])
    for cmd in commands:subprocess.run(cmd,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    expected = 'Bearer oda-synthetic-'+uuid.uuid4().hex
    bundle=case.directory/'ca-bundle.pem'
    system_ca=ssl.get_default_verify_paths().cafile
    bundle.write_bytes((Path(system_ca).read_bytes() if system_ca else b'')+b'\n'+ca.read_bytes())
    case.env.update(SSL_CERT_FILE=str(bundle),NODE_EXTRA_CA_CERTS=str(ca),REQUESTS_CA_BUNDLE=str(bundle))
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self):
            self.send_response(405);self.end_headers()
        def do_POST(self):
            authorized = not authenticated or self.headers.get('Authorization') == expected
            extended_server.record(case.log,{'event':'http-request','authorized':authorized,'authorizationPresent':'Authorization' in self.headers,'tls':True})
            if not authorized:
                self.send_response(401);self.send_header('Content-Length','0');self.end_headers();return
            request=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
            response=extended_server.respond(request,case.log)
            body=json.dumps(response).encode() if response is not None else b''
            self.send_response(200 if response is not None else 202)
            self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)))
            self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(cert,key)
    server.socket=tls.wrap_socket(server.socket,server_side=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try: yield f'https://localhost:{server.server_port}/mcp',expected
    finally:server.shutdown();server.server_close();thread.join()


def remote(case: Case, authenticated=False):
    with remote_peer(case,authenticated) as (url,token):
        server={'type':'remote','url':url}
        if authenticated:
            server['headers']={'Authorization':'urn:open-dot-agents:env:ODA_REMOTE_AUTH'}
            case.env['ODA_REMOTE_AUTH']=token
        write(case.root/'.agents/tools/mcp.json',{'mcpServers':{'oda-marker':server}})
        if authenticated and case.vendor=='copilot':
            case.apply(expect_refusal=True);return
        case.apply();_,markers=case.run();case.call_observed(markers)
        requests=[e for e in events(case.log) if e.get('event')=='http-request']
        case.check('https-requests',bool(requests) and all(e['authorized'] for e in requests),requests)
        generated=(case.root/('.codex/config.toml' if case.vendor=='codex' else '.github/mcp.json')).read_text()
        case.check('no-literal-auth-in-projection',token not in generated)


def stdio_environment(case: Case):
    sentinel='synthetic-'+uuid.uuid4().hex
    arguments=['two words','literal;echo ODA_BAD','$(touch ODA_BAD)','quote"value','Unicode-è']
    case.stdio(sentinel,arguments);case.env['ODA_NATIVE_ENV']=sentinel
    if case.vendor=='copilot':case.apply(expect_refusal=True);return
    case.apply();_,markers=case.run();case.call_observed(markers)
    observations=events(case.log)
    case.check('stdio-argv-preserved',any(e.get('argv')==arguments for e in observations),observations)
    case.check('stdio-env-resolved',any(e.get('environmentMatches') is True for e in observations))
    case.check('no-shell-interpolation',not (case.root/'ODA_BAD').exists())


HOOK_EVENTS = ['SessionStart','SessionEnd','UserPromptSubmit','PreToolUse','PostToolUse','PermissionRequest',
               'Stop','SubagentStart','SubagentStop','PreCompact']


def hook_handler(case: Case, tag: str, delay=0, timeout=None, decision=None):
    command = ['python3',str(case.helpers/'hook_probe.py'),str(case.directory/'hooks.jsonl'),tag,str(delay)]
    if decision:command.append(decision)
    handler = {'type':'command','command':shlex.join(command)}
    if timeout is not None:handler['timeoutSec']=timeout
    return handler


def install_hooks(case: Case, document: dict):
    write(case.root/'.agents/manifest.json',{'version':'1.0.0','profiles':['tools','hooks']})
    write(case.root/'.agents/hooks/hooks.json',{'hooks':document})


def filtered_hooks(case: Case, event: str, handler=None, matcher='.*'):
    return [{'matcher':matcher,'hooks':[handler or hook_handler(case,event)]},
            {'matcher':'^ODA_NEVER_MATCH$','hooks':[hook_handler(case,event+'.no-match')]}]


def check_no_filter_leak(case: Case, observations):
    case.check('nonmatching-event-hooks-skipped',not any(e['tag'].endswith('.no-match') for e in observations),observations)


def lifecycle(case: Case):
    document={event:[{'hooks':[hook_handler(case,event)]}] for event in HOOK_EVENTS}
    if case.vendor=='codex':
        for event in ['SessionStart','SessionEnd']:document[event]=filtered_hooks(case,event)
    install_hooks(case,document)
    case.apply();_,markers=case.run();case.call_observed(markers)
    observations=events(case.directory/'hooks.jsonl')
    check_no_filter_leak(case,observations)
    case.check('native-hook-input-json',bool(observations) and all(e.get('jsonObject') for e in observations if e['phase']=='start'),observations)
    for event in ['SessionStart','SessionEnd','UserPromptSubmit','PreToolUse','PostToolUse','Stop']:
        case.check('event.'+event,any(e['tag']==event and e['phase']=='end' for e in observations))


def matchers(case: Case):
    groups=[{'matcher':pattern,'hooks':[hook_handler(case,tag)]} for tag,pattern in
            [('match','^.*record$'),('no-match','^ODA_NONEXISTENT_TOOL$')]]
    install_hooks(case,{'PreToolUse':groups,'PostToolUse':groups})
    case.apply();_,markers=case.run();case.call_observed(markers)
    observations=events(case.directory/'hooks.jsonl')
    case.check('regex-match',len([e for e in observations if e['tag']=='match' and e['phase']=='end'])==2,observations)
    case.check('regex-nonmatch',not any(e['tag']=='no-match' for e in observations))


def hook_timeout(case: Case):
    install_hooks(case,{'PreToolUse':[{'matcher':'^.*record$','hooks':[hook_handler(case,'timeout',4,1)]}]})
    case.apply();_,markers=case.run();case.call_observed(markers)
    observations=events(case.directory/'hooks.jsonl')
    case.check('timeout-started',any(e['phase']=='start' for e in observations),observations)
    case.check('timeout-stopped-command',not any(e['phase']=='end' for e in observations))
    starts=[e['at'] for e in observations if e['phase']=='start']
    calls=[e['at'] for e in events(case.log) if e.get('event')=='tool-call']
    elapsed=calls[0]-starts[0] if starts and calls else None
    case.check('one-second-timeout-enforced',elapsed is not None and .7<=elapsed<3.5,elapsed)


def instructions(case: Case):
    child,sibling=case.root/'packages/child',case.root/'packages/sibling'
    child.mkdir(parents=True);sibling.mkdir(parents=True)
    nested='nested-'+uuid.uuid4().hex
    write(child/'AGENTS.md','# Child instructions\n\nFor ODA_EXTENDED, replace the parent marker with `'+nested+'`.\nCall only the native oda-marker record tool, exactly once.\n')
    case.apply()
    _,markers=case.run(cwd=child);case.call_observed(markers,nested)
    case.check('nearest-instruction-overrides-parent',case.marker not in markers)
    _,markers=case.run(cwd=sibling);case.call_observed(markers)
    case.check('sibling-scope-isolation',nested not in markers)


def skill_resources(case: Case):
    skill=case.root/'.agents/skills/resource-probe'
    write(case.root/'.agents/manifest.json',{'version':'1.0.0','profiles':['tools','skills']})
    write(case.root/'.agents/AGENTS.md','# Test\n\nFor ODA_RESOURCE, use the resource-probe skill. Use native MCP only for record.\n')
    write(skill/'SKILL.md',"""---
name: resource-probe
description: Use for ODA_RESOURCE requests.
---
Read references/steps.md, then run scripts/emit.py as it directs.
Call native oda-marker record once with the resulting marker.
Do not execute the MCP server or write its log.
""")
    write(skill/'references/steps.md','Run the emit.py script with Python 3. It reads assets/value with spaces.json relative to its own location.\n')
    write(skill/'assets/value with spaces.json',{'marker':case.marker})
    write(skill/'scripts/emit.py',"from pathlib import Path\nimport json\np=Path(__file__).resolve().parents[1]/'assets/value with spaces.json'\nprint(json.loads(p.read_text())['marker'])\n")
    case.apply();_,markers=case.run('ODA_RESOURCE. Complete the configured resource check.');case.call_observed(markers)
    outputs=[t.get('output','') for t in case.transcripts if t['kind']=='native']
    case.check('skill-script-used',any('emit.py' in t for t in outputs))



def profile_tools(case: Case):
    for phase,profiles in [('initial-off',[]),('enabled',['tools']),('removed',[]),('enabled-again',['tools'])]:
        write(case.root/'.agents/manifest.json',{'version':'1.0.0','profiles':profiles})
        case.apply();before=len(events(case.log));_,markers=case.run()
        new=events(case.log)[before:]
        if profiles:
            case.check(phase,case.marker in markers and any(e.get('event')=='tool-call' for e in new),new)
        else:
            case.check(phase,not markers and not new,new)


def profile_skills(case: Case):
    skill_marker='skill-'+uuid.uuid4().hex
    write(case.root/'.agents/skills/profile-probe/SKILL.md','---\nname: profile-probe\ndescription: Use for ODA_PROFILE requests.\n---\nCall native oda-marker record once with marker `'+skill_marker+'`.\n')
    write(case.root/'.agents/AGENTS.md','# Instructions\n\nFor ODA_PROFILE, use profile-probe only if it is in the native skill list.\nDo not search for or read undiscovered skill files. If it is not available,\ncall native oda-marker record once with marker `'+case.marker+'`.\n')
    for phase,profiles in [('initial-off',['tools']),('enabled',['tools','skills']),('removed',['tools'])]:
        write(case.root/'.agents/manifest.json',{'version':'1.0.0','profiles':profiles})
        case.apply();_,markers=case.run('ODA_PROFILE. Follow the configured skill availability rule.')
        expected=skill_marker if 'skills' in profiles else case.marker
        unexpected=case.marker if 'skills' in profiles else skill_marker
        case.check(phase,expected in markers and unexpected not in markers,{'nativeMarkers':markers,'expected':expected})


def untrusted(case: Case):
    install_hooks(case,{'SessionStart':[{'hooks':[hook_handler(case,'untrusted')]}]})
    case.apply()
    if case.vendor=='copilot':
        path=Path(case.env['COPILOT_HOME'])/'config.json';config=json.loads(path.read_text());config['trustedFolders']=[];write(path,config)
    cmd=native.command(case.vendor,case.executable,case.root,'ODA_EXTENDED. Follow the applicable instructions.')
    if case.vendor=='codex':cmd.remove('--dangerously-bypass-hook-trust')
    _,markers=case.run(command=cmd)
    observed=events(case.directory/'hooks.jsonl')
    case.check('untrusted-hook-not-executed',not observed,observed)
    if case.vendor=='copilot':case.check('untrusted-mcp-not-loaded',not events(case.log) and not markers,events(case.log))
    else:case.call_observed(markers)


def refusal_boundaries(case: Case):
    manifests=[{'version':'1.0.0','profiles':['unknown-profile']},
               {'version':'1.0.0','profiles':['tools'],'requires':['unknown.capability']}]
    for manifest in manifests:
        write(case.root/'.agents/manifest.json',manifest);case.apply(expect_refusal=True)
    write(case.root/'.agents/manifest.json',{'version':'1.0.0','profiles':['tools','hooks']})
    for hooks in [{'UnknownEvent':[{'hooks':[hook_handler(case,'bad')]}]},
                  {'Stop':[{'matcher':'not-supported','hooks':[hook_handler(case,'bad')]}]}]:
        write(case.root/'.agents/hooks/hooks.json',{'hooks':hooks});case.apply(expect_refusal=True)
    unsupported=['UserPromptSubmit','Stop']
    if case.vendor=='copilot':unsupported+=['SessionStart','SessionEnd','SubagentStop']
    for event in unsupported:
        write(case.root/'.agents/hooks/hooks.json',{'hooks':{event:[{'matcher':'.*','hooks':[hook_handler(case,'bad')]}]}})
        case.apply(expect_refusal=True)
    write(case.root/'.agents/manifest.json',{'version':'1.0.0','profiles':['tools']})
    case.stdio('synthetic')
    catalogue=json.loads((case.root/'.agents/tools/mcp.json').read_text())
    catalogue['mcpServers']['oda-marker']['env']['ODA_NATIVE_ENV']='urn:open-dot-agents:env:ODA_OTHER_ENV'
    write(case.root/'.agents/tools/mcp.json',catalogue);case.apply(expect_refusal=True)
    case.check('no-native-launch',not case.log.exists())



def subagents(case: Case):
    document={'SubagentStart':filtered_hooks(case,'SubagentStart'),'SubagentStop':[{'hooks':[hook_handler(case,'SubagentStop')]}]}
    if case.vendor=='codex':document['SubagentStop']=filtered_hooks(case,'SubagentStop')
    install_hooks(case,document)
    write(case.root/'payload.txt',case.marker+'\n')
    case.apply()
    prompt=('This is a native subagent lifecycle test. Delegate exactly one read-only task to one explore subagent: '
            'read payload.txt and return its exact contents. Wait for the subagent to finish. '
            'Then the parent must call native oda-marker record once with that content as marker. '
            'Do not read payload.txt in the parent. Do not execute marker scripts or write logs.')
    cmd=native.command(case.vendor,case.executable,case.root,prompt)
    if case.vendor=='codex':
        cmd.remove('--ephemeral')
        cmd=cmd[:-1]+['--enable','multi_agent',cmd[-1]]
    _,markers=case.run(command=cmd);case.call_observed(markers)
    observations=events(case.directory/'hooks.jsonl')
    check_no_filter_leak(case,observations)
    for event in ['SubagentStart','SubagentStop']:
        case.check('event.'+event,any(e['tag']==event and e['phase']=='end' for e in observations),observations)


def permission_request(case: Case):
    install_hooks(case,{'PermissionRequest':filtered_hooks(case,'PermissionRequest',hook_handler(case,'permission',decision='permission-'+case.vendor))})
    case.apply();cmd=native.command(case.vendor,case.executable,case.root,'ODA_EXTENDED. Follow the instructions; the configured permission hook will approve the marker call.')
    if case.vendor=='copilot':cmd.remove('--allow-all')
    else:
        cmd.remove('--dangerously-bypass-approvals-and-sandbox')
        cmd=cmd[:-1]+['--sandbox','read-only','-c','approval_policy="on-request"',cmd[-1]]
    if case.vendor=='codex':
        write(Path(case.env['CODEX_HOME'])/'config.toml','[projects.'+json.dumps(str(case.root))+']\ntrust_level = "trusted"\n')
        path=case.root/'.codex/config.toml'
        path.write_text(path.read_text().replace('[mcp_servers.oda-marker]',"[mcp_servers.oda-marker]\ndefault_tools_approval_mode = 'prompt'"))
    _,markers=case.run(command=cmd);case.call_observed(markers)
    observed=events(case.directory/'hooks.jsonl')
    check_no_filter_leak(case,observed)
    case.check('event.PermissionRequest',any(e['tag']=='permission' and e['phase']=='end' for e in observed),observed)


def tool_denial(case: Case):
    install_hooks(case,{'PreToolUse':[{'matcher':'^.*record$','hooks':[hook_handler(case,'deny',decision='deny-'+case.vendor)]}]})
    case.apply();_,markers=case.run('ODA_EXTENDED. Attempt the required native tool once. If policy denies it, report the refusal and stop; do not retry or use a workaround.')
    observed=events(case.directory/'hooks.jsonl')
    case.check('denial-hook-fired',any(e['tag']=='deny' and e['phase']=='end' for e in observed),observed)
    case.check('denied-tool-not-executed',not markers and not any(e.get('event')=='tool-call' for e in events(case.log)))


def persisted_start(case: Case):
    cmd=native.command(case.vendor,case.executable,case.root,'ODA_EXTENDED. Follow the applicable instructions.')
    session=str(uuid.uuid4())
    if case.vendor=='codex':cmd.remove('--ephemeral')
    else:cmd=cmd[:-2]+['--session-id='+session,*cmd[-2:]]
    process,markers=case.run(command=cmd);case.call_observed(markers)
    if case.vendor=='codex':
        for line in process.stdout.splitlines():
            try:event=json.loads(line)
            except ValueError:continue
            if event.get('type')=='thread.started':session=event['thread_id'];break
        else:raise RuntimeError('Codex did not report a persisted thread id')
    return session


def resume_command(case: Case, session: str, prompt: str):
    if case.vendor=='codex':
        return [case.executable,'exec','resume','--json','--dangerously-bypass-approvals-and-sandbox',
                '--dangerously-bypass-hook-trust','--skip-git-repo-check',session,prompt]
    return [case.executable,'-C',str(case.root),'--resume',session,'--no-auto-update','--allow-all','--output-format','json','-p',prompt]


def compaction(case: Case):
    install_hooks(case,{'PreCompact':filtered_hooks(case,'PreCompact',matcher='manual')})
    case.apply();session=persisted_start(case)
    if case.vendor=='codex':
        write(Path(case.env['CODEX_HOME'])/'config.toml','[projects.'+json.dumps(str(case.root))+']\ntrust_level = "trusted"\n')
        result=codex_terminal.compact_tui(case.executable,case.root,case.env,session,case.directory/'hooks.jsonl')
        case.transcripts.append({'kind':'native-terminal',**result})
        case.check('native-compaction-completed',result['completed'])
    else:case.run(command=resume_command(case,session,'/compact'))
    observed=events(case.directory/'hooks.jsonl')
    check_no_filter_leak(case,observed)
    case.check('event.PreCompact',any(e['tag']=='PreCompact' and e['phase']=='end' for e in observed),observed)


def resumed_refresh(case: Case):
    install_hooks(case,{'SessionStart':[{'hooks':[hook_handler(case,'before')]}]})
    case.apply();session=persisted_start(case)
    old=case.marker;case.marker='after-'+uuid.uuid4().hex
    write(case.root/'.agents/AGENTS.md','# Instructions\n\nFor ODA_EXTENDED, call native oda-marker record once with `'+case.marker+'`. This replaces the previous marker.\n')
    install_hooks(case,{'SessionStart':[{'hooks':[hook_handler(case,'after')]}]})
    case.apply();before=len(events(case.directory/'hooks.jsonl'))
    _,markers=case.run(command=resume_command(case,session,'ODA_EXTENDED. Follow the current repository instructions.'))
    case.call_observed(markers);case.check('resumed-instructions-refreshed',old not in markers)
    observed=events(case.directory/'hooks.jsonl')[before:]
    case.check('resumed-hooks-refreshed',any(e['tag']=='after' for e in observed) and not any(e['tag']=='before' for e in observed),observed)



def missing_environment(case: Case):
    case.stdio('synthetic-required-value')
    case.env.pop('ODA_NATIVE_ENV',None)
    if case.vendor=='copilot':case.apply(expect_refusal=True);return
    case.apply();_,markers=case.run()
    observed=events(case.log)
    case.check('missing-env-fails-activation',not markers and not any(e.get('event')=='startup' for e in observed),observed)


def profile_hooks(case: Case):
    install_hooks(case,{'SessionStart':[{'hooks':[hook_handler(case,'selected-hook')]}]})
    for phase,profiles in [('initial-off',['tools']),('enabled',['tools','hooks']),('removed',['tools'])]:
        write(case.root/'.agents/manifest.json',{'version':'1.0.0','profiles':profiles});case.apply()
        before=len(events(case.directory/'hooks.jsonl'));_,markers=case.run()
        case.check(phase+'.native-mcp-call',case.marker in markers)
        new=events(case.directory/'hooks.jsonl')[before:]
        expected='hooks' in profiles
        case.check(phase+'.hook-selection',bool(new)==expected,new)



def hook_exit_codes(case: Case):
    for code in (1,2):
        handler=hook_handler(case,'exit'+str(code),decision='exit'+str(code))
        if code==1:handler['command']+='; exit 1'
        install_hooks(case,{'PreToolUse':[{'matcher':'^.*record$','hooks':[handler]}]})
        case.apply();before=len(events(case.log))
        _,markers=case.run('ODA_EXTENDED. Attempt the native tool once. If denied, stop without retries or workarounds.')
        new=events(case.log)[before:]
        called=case.marker in markers and any(e.get('event')=='tool-call' for e in new)
        allowed=code==1 and case.vendor=='codex'
        case.check('exit'+str(code)+('.fail-open' if allowed else '.denies'),called if allowed else not called,new)


def resumed_resources(case: Case):
    case.apply();session=persisted_start(case)
    case.marker='refresh-'+uuid.uuid4().hex
    case.stdio(args=['updated-config'])
    write(case.root/'.agents/manifest.json',{'version':'1.0.0','profiles':['tools','skills']})
    write(case.root/'.agents/AGENTS.md','# Instructions\n\nFor ODA_NEW_SKILL use the new-resource skill. Use only the native skill list.\nDo not scan for skill files or execute marker servers.\n')
    skill=case.root/'.agents/skills/new-resource'
    write(skill/'SKILL.md','---\nname: new-resource\ndescription: Use for ODA_NEW_SKILL.\n---\nRead assets/marker.txt and call native oda-marker record with its exact content.\n')
    write(skill/'assets/marker.txt',case.marker)
    case.apply();before=len(events(case.log))
    _,markers=case.run(command=resume_command(case,session,'ODA_NEW_SKILL. Follow the current instructions.'))
    case.call_observed(markers)
    case.check('resumed-mcp-config-refreshed',any(e.get('argv')==['updated-config'] for e in events(case.log)[before:]),events(case.log)[before:])
    case.check('resumed-new-skill-resource',case.marker in markers)



def remote_missing_environment(case: Case):
    with remote_peer(case,True) as (url,_):
        write(case.root/'.agents/tools/mcp.json',{'mcpServers':{'oda-marker':{'type':'remote','url':url,
              'headers':{'Authorization':'urn:open-dot-agents:env:ODA_REMOTE_AUTH'}}}})
        case.env.pop('ODA_REMOTE_AUTH',None)
        if case.vendor=='copilot':case.apply(expect_refusal=True);return
        case.apply();_,markers=case.run()
        observed=events(case.log)
        case.check('missing-header-env-fails-activation',not markers and not any(e.get('event')=='tool-call' for e in observed),observed)
        case.check('no-empty-or-literal-authorization',not any(e.get('authorizationPresent') for e in observed))



def stdio_arguments(case: Case):
    arguments=['two words','literal;echo ODA_BAD','$(touch ODA_BAD)','quote"value','Unicode-è']
    case.stdio(args=arguments)
    command=case.helpers/'python with spaces';command.symlink_to(sys.executable)
    path=case.root/'.agents/tools/mcp.json';catalogue=json.loads(path.read_text())
    catalogue['mcpServers']['oda-marker']['command']=str(command);write(path,catalogue)
    case.apply();_,markers=case.run();case.call_observed(markers)
    case.check('stdio-argv-preserved',any(e.get('argv')==arguments for e in events(case.log)),events(case.log))
    case.check('no-shell-interpolation',not (case.root/'ODA_BAD').exists())



CASES={'remote-https':lambda c:remote(c), 'remote-auth-env':lambda c:remote(c,True), 'stdio-env-argv':stdio_environment, 'hook-lifecycle':lifecycle, 'hook-matchers':matchers, 'hook-timeout':hook_timeout, 'instruction-precedence':instructions, 'skill-resources':skill_resources, 'profile-tools':profile_tools, 'profile-skills':profile_skills, 'untrusted':untrusted, 'refusal-boundaries':refusal_boundaries, 'subagent-events':subagents, 'permission-request':permission_request, 'tool-denial':tool_denial, 'compaction':compaction, 'resumed-refresh':resumed_refresh, 'missing-env':missing_environment, 'profile-hooks':profile_hooks, 'hook-exit-codes':hook_exit_codes, 'resumed-resources':resumed_resources, 'remote-missing-env':remote_missing_environment, 'stdio-argv':stdio_arguments}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('vendor',choices=['codex','copilot'])
    parser.add_argument('--case',choices=list(CASES),action='append');parser.add_argument('--output',type=Path,default=EVIDENCE)
    parser.add_argument('--auth',choices=['auto','environment','existing-login'],default='auto')
    args=parser.parse_args();checks,metadata=native.preflight(args.vendor,args.auth)
    for name,data in SNAPSHOTS.items():
        source=args.output/'sources'/(SOURCES[name]+'-'+name)
        source.parent.mkdir(parents=True,exist_ok=True)
        if not source.exists():source.write_bytes(data)
    if not all(c['passed'] for c in checks):print(json.dumps(checks));return 2
    overall=True
    for case_id in args.case or CASES:
        print(f'{args.vendor}: START {case_id}',flush=True)
        record={'vendor':args.vendor,'case':case_id,'package':native.VERSIONS['harnesses'][args.vendor],
                'sources':SOURCES,'preflight':checks,'startedAt':datetime.now(UTC).isoformat()}
        try:
            with tempfile.TemporaryDirectory(prefix='oda-extended-'+args.vendor+'-') as temporary:
                case=Case(args.vendor,case_id,Path(temporary),os.environ['AGENTS_BIN'],metadata['harnessPath'],metadata)
                try:CASES[case_id](case)
                except Exception as e:case.check('runner-completed',False,str(e))
                record.update(metadata=case.metadata,helperHashes=case.helper_hashes,checks=case.checks,transcripts=case.transcripts,
                              observations=events(case.log),passed=bool(case.checks) and all(c['passed'] for c in case.checks))
        except Exception as e:record.update(passed=False,error=str(e))
        record['completedAt']=datetime.now(UTC).isoformat()
        record['outcome'] = ('adapter-refusal' if record.get('passed') and not any(t['kind'].startswith('native') for t in record.get('transcripts',[])) else 'native-pass' if record.get('passed') else 'runner-incomplete' if any(c['id']=='runner-completed' and not c['passed'] for c in record.get('checks',[])) else 'native-failure')
        path=args.output/args.vendor/(case_id+'.json')
        if path.exists():
            previous=json.loads(path.read_text())
            stamp=previous['startedAt'].replace(':','-')
            write(args.output/args.vendor/'attempts'/(case_id+'-'+stamp+'.json'),previous)
        write(path,record)
        overall &= record['passed'];print(f'{args.vendor}: {case_id}: {"PASS" if record["passed"] else "FAIL"}',flush=True)
    return 0 if overall else 1


if __name__=='__main__':raise SystemExit(main())
