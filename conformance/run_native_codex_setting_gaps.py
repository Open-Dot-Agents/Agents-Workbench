#!/usr/bin/env python3
"""Retain native behavior for documented settings absent from pinned types."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import traceback

from run_native_approvals import Client, PINS, sha, native_binary


CASES = {
    'baseline': '',
    'reasoning-false': 'model_supports_reasoning_summaries=false\n',
    'reasoning-invalid': 'model_supports_reasoning_summaries="invalid-type"\n',
    'rollout-interval': '[features.rollout_budget]\nenabled=true\nlimit_tokens=1000\nreminder_at_remaining_tokens=[100]\nreminder_interval_tokens=100\n',
    'rollout-control': '[features.rollout_budget]\nenabled=true\nlimit_tokens=1000\nreminder_at_remaining_tokens=[100]\n',
    'keymap-invalid': '[tui.keymap.global]\nopen_external_editor="ctrl+y"\n',
    'mcp-remote': '[mcp_servers.fixture]\ncommand="/usr/bin/python3"\nargs=["SERVER"]\nexperimental_environment="remote"\n',
    'mcp-local': '[mcp_servers.fixture]\ncommand="/usr/bin/python3"\nargs=["SERVER"]\n',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=CASES, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    snapshot = output.with_suffix('.runner.py')
    assert not output.exists() and not snapshot.exists(), 'refuse evidence replacement'
    binary = native_binary('codex')
    assert sha(binary) == PINS['codex']
    with snapshot.open('xb') as stream: stream.write(Path(__file__).read_bytes())
    root = Path(tempfile.mkdtemp(prefix='agents-codex-gaps-'))
    home, workspace = root/'home', root/'workspace'
    home.mkdir(mode=0o700); workspace.mkdir(mode=0o700)
    subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
    requests, errors = [], []
    result = {'case': args.case, 'fixture': str(root), 'native_version': '0.154.0', 'native_sha256': sha(binary),
              'runner_sha256': sha(snapshot), 'helper_sha256': sha(Path(__file__).with_name('run_native_approvals.py')),
              'full_adapter_support': False, 'adapter_mapping': False}

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            try:
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append(request)
                item = {'type': 'message', 'role': 'assistant', 'id': 'fixture-message', 'content': [{'type': 'output_text', 'text': 'AGENTS_SETTING_PROBE_DONE'}]}
                events = [{'type': 'response.created', 'response': {'id': 'fixture'}}, {'type': 'response.output_item.done', 'item': item},
                          {'type': 'response.completed', 'response': {'id': 'fixture', 'usage': {'input_tokens': 3, 'output_tokens': 2, 'total_tokens': 5}}}]
                data = ''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events).encode()
                self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
            except Exception as error: errors.append(str(error))

    server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    mcp = root/'server.py'; effect = root/'mcp-local-effect.json'
    mcp.write_text('import sys,json,os\nfrom pathlib import Path\n'
                   f'Path({str(effect)!r}).write_text(json.dumps({{"pid":os.getpid(),"parent":os.getppid(),"cwd":os.getcwd()}}))\n'
                   'for line in sys.stdin:\n r=json.loads(line)\n'
                   ' if "id" not in r: continue\n'
                   ' v={"protocolVersion":"2024-11-05","capabilities":{"tools":{}},"serverInfo":{"name":"fixture","version":"1"}} if r["method"]=="initialize" else {"tools":[]}\n'
                   ' print(json.dumps({"jsonrpc":"2.0","id":r["id"],"result":v}),flush=True)\n')
    config = ('model="gpt-5.4"\nmodel_provider="fixture"\nmodel_reasoning_summary="auto"\n'+CASES[args.case].replace('SERVER', str(mcp))+
              f'\n[analytics]\nenabled=false\n[features]\nremote_plugin=false\nrecommended_plugins=false\napps=false\nenable_request_compression=false\nrespect_system_proxy=false\n'
              f'[model_providers.fixture]\nname="Fixture"\nbase_url="http://127.0.0.1:{server.server_port}/v1"\nwire_api="responses"\nrequires_openai_auth=false\nsupports_websockets=false\n[projects.{json.dumps(str(workspace))}]\ntrust_level="trusted"\n')
    (home/'config.toml').write_text(config)
    result['config'] = config
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'CODEX_HOME': str(home), 'XDG_STATE_HOME': str(root/'state')}
    client = Client([str(binary), 'app-server', '--listen', 'stdio://'], workspace, env, 'deny', str(root/'unused'))
    result['native_pid'] = client.process.pid
    try:
        deadline = time.monotonic()+40
        result['initialize'] = client.response(client.request('initialize', {'clientInfo': {'name': 'agents-setting-gaps', 'version': '0'}, 'capabilities': {'experimentalApi': True}}), deadline)
        client.send({'method': 'initialized'})
        result['config_read'] = client.response(client.request('config/read', {'cwd': str(workspace), 'includeLayers': True}), deadline)
        thread = client.response(client.request('thread/start', {'cwd': str(workspace), 'ephemeral': True}), deadline)
        result['thread'] = thread
        thread_id = thread['thread']['id']
        client.response(client.request('turn/start', {'threadId': thread_id, 'input': [{'type': 'text', 'text': 'AGENTS_SETTING_PROBE_REQUEST'}]}), deadline)
        while True:
            event = client.receive(deadline)
            if event.get('method') == 'turn/completed': result['completion'] = event; break
        assert result['completion']['params']['turn']['status'] == 'completed'
        assert requests and not errors
        assert any('AGENTS_SETTING_PROBE_DONE' in json.dumps(e) and thread_id in json.dumps(e) for e in client.events)
        result['native_completed'] = True
    except Exception as error:
        result.update(native_completed=False, error=str(error), traceback=traceback.format_exc())
    finally:
        client.close(); server.shutdown(); server.server_close()
        result.update(events=client.events, approvals=client.approvals, stderr=client.errors, model_requests=requests, provider_errors=errors,
                      mcp_effect=json.loads(effect.read_text()) if effect.exists() else None)
        result['config_unchanged'] = (home/'config.toml').read_text() == config
        with output.open('x') as stream: json.dump(result, stream, indent=2); stream.write('\n')
    print(json.dumps({'case': args.case, 'native_completed': result['native_completed'], 'error': result.get('error'), 'output': str(output)}))
    # A rejected configuration is an observation, not a successful mapping.
    return 0


if __name__ == '__main__': raise SystemExit(main())
