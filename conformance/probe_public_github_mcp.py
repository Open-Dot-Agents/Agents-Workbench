#!/usr/bin/env python3
"""Perform a redacted, read-only handshake with the public GitHub MCP service."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.error
import urllib.request
import uuid


def redact_credential_result(result, token):
    if token and token in json.dumps(result):
        return {'passed': False, 'error': 'credential appeared in evidence payload',
                'credential_value_stored': False, 'credential_sha256_stored': False,
                'remote_mutations': False}
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();output=args.output.resolve();snapshot=output.with_suffix('.runner.py')
    assert not output.exists() and not snapshot.exists(),'refuse evidence replacement'
    with snapshot.open('xb') as f:f.write(Path(__file__).read_bytes())
    token_process=subprocess.run(['gh','auth','token'],capture_output=True,text=True,timeout=15)
    token=token_process.stdout.strip()
    assert token_process.returncode==0 and token,'authenticated gh token unavailable'
    assert '\n' not in token and '\r' not in token,'invalid token transport'
    endpoint='https://api.githubcopilot.com/mcp/'
    result={'endpoint':endpoint,'operation':'initialize and tools/list only','credential_source':'gh auth keyring',
            'credential_value_stored':False,'credential_sha256_stored':False,'remote_mutations':False,
            'runner_sha256':hashlib.sha256(snapshot.read_bytes()).hexdigest(),'requests':[],'passed':False}
    session=None
    try:
        calls=[('initialize',{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'open-dot-agents-read-only-probe','version':'0'}}),('tools/list',{})]
        for identifier,(method,params) in enumerate(calls,1):
            body=json.dumps({'jsonrpc':'2.0','id':identifier,'method':method,'params':params}).encode()
            headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json, text/event-stream','User-Agent':'Open-Dot-Agents-read-only-evidence'}
            if session:headers['Mcp-Session-Id']=session
            request=urllib.request.Request(endpoint,data=body,headers=headers,method='POST')
            try:
                with urllib.request.urlopen(request,timeout=30) as response:
                    raw=response.read();status=response.status;response_headers=dict(response.headers.items())
            except urllib.error.HTTPError as error:
                raw=error.read();status=error.code;response_headers=dict(error.headers.items())
            session=response_headers.get('Mcp-Session-Id') or response_headers.get('mcp-session-id') or session
            text=raw.decode('utf-8','replace')
            # Store no response headers because proxies can add credential data.
            record={'method':method,'status':status,'content_type':response_headers.get('Content-Type',''),
                    'body_sha256':hashlib.sha256(raw).hexdigest(),'body':text,'session_returned':bool(session)}
            result['requests'].append(record)
            assert token not in text,'credential appeared in response'
            assert status==200,record
        body=result['requests'][1]['body']
        if body.startswith('event:'):
            payloads=[line[6:] for line in body.splitlines() if line.startswith('data: ')]
            decoded=[json.loads(value) for value in payloads if value!='[DONE]']
            tools=next(item['result']['tools'] for item in decoded if item.get('id')==2)
        else:tools=json.loads(body)['result']['tools']
        result['tool_count']=len(tools)
        result['tool_names']=[tool['name'] for tool in tools]
        assert tools and all(isinstance(tool.get('inputSchema'),dict) for tool in tools)
        result['passed']=True
    except Exception as error:
        result.update(error=type(error).__name__+': '+str(error))
    finally:
        result = redact_credential_result(result, token)
        token=''
        with output.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    print(json.dumps({'passed':result['passed'],'statuses':[r['status'] for r in result.get('requests', [])],'tool_count':result.get('tool_count'),'error':result.get('error')}))
    return 0 if result['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
