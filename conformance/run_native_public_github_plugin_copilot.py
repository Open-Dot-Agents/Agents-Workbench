#!/usr/bin/env python3
"""Load the shared GitHub plugin with pinned Copilot and its public MCP service."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import traceback

from run_native_approvals import Client, PINS, sha, native_binary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(); output = args.output.resolve(); snapshot = output.with_suffix(".runner.py")
    assert not output.exists() and not snapshot.exists(), "refuse evidence replacement"
    binary = native_binary('copilot'); assert sha(binary) == PINS["copilot"]
    snapshot.write_bytes(Path(__file__).read_bytes())
    token_run = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=15)
    token = token_run.stdout.strip(); assert token_run.returncode == 0 and token and "\n" not in token and "\r" not in token
    repo = Path(__file__).resolve().parents[2]
    source = repo / ".agents/plugins/com.openai.codex/plugins/github"
    provenance = json.loads((source.parents[1] / "provenance.json").read_text())
    assert all(sha(source / name) == digest for name, digest in provenance["files"].items())
    base = Path(tempfile.mkdtemp(prefix="agents-public-github-copilot-"))
    home, workspace, market = base / "home", base / "workspace", base / "market"
    for path in (home, workspace, market): path.mkdir(mode=0o700)
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    shutil.copytree(source, market / "github")
    catalog = {"name":"agents-public-github","owner":{"name":"Open-Dot-Agents fixture"},
               "plugins":[{"name":"github","source":"./github"}]}
    for path in (market / "marketplace.json", market / ".agents/plugins/marketplace.json"):
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(catalog))
    requests = []; errors = []
    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            try:
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"]))); requests.append(request)
                data = json.dumps({"id":"fixture-response","object":"chat.completion","created":1,
                    "model":"fixture-model","choices":[{"index":0,"finish_reason":"stop",
                    "message":{"role":"assistant","content":"AGENTS_PUBLIC_GITHUB_COPILOT_READY"}}],
                    "usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
            except Exception as error: errors.append(str(error))
    server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    settings = {"trustedFolders":[str(workspace)],"autoUpdate":False,
                "telemetry":{"enabled":False},"tips":{"enabled":False}}
    (home / "config.json").write_text(json.dumps(settings)); (home / "config.json").chmod(0o600)
    env = {"PATH":"/usr/bin:/bin","HOME":str(home),"COPILOT_HOME":str(home),
           "COPILOT_CACHE_HOME":str(base / "cache"),"XDG_STATE_HOME":str(base / "state"),
           "COPILOT_OFFLINE":"true","COPILOT_PROVIDER_TYPE":"openai",
           "COPILOT_PROVIDER_WIRE_API":"completions",
           "COPILOT_PROVIDER_BASE_URL":f"http://127.0.0.1:{server.server_port}/v1",
           "COPILOT_MODEL":"fixture-model","GITHUB_PAT_TOKEN":token}
    result = {"native_version":"1.0.83","native_sha256":sha(binary),"runner_sha256":sha(snapshot),
              "helper_sha256":sha(Path(__file__).with_name("run_native_approvals.py")),"fixture":str(base),
              "source_revision":provenance["revision"],"source_files":provenance["files"],
              "source_transformations":provenance["transformations"],
              "public_endpoint":"https://api.githubcopilot.com/mcp/","credential_source":"gh auth keyring",
              "credential_value_stored":False,"credential_sha256_stored":False,
              "remote_operation":"MCP initialization and tools/list through native client; model calls no GitHub tool",
              "external_model":False,"external_github_mcp":True,"remote_mutations":False,
              "full_adapter_support":False,"commands":[],"passed":False}
    client = None
    try:
        for command in ([str(binary),"plugin","marketplace","add",str(market)],
                        [str(binary),"plugin","install","github@agents-public-github"]):
            run = subprocess.run(command, cwd=workspace, env=env, capture_output=True, text=True, timeout=45)
            result["commands"].append({"command":command,"exit_code":run.returncode,"stdout":run.stdout,"stderr":run.stderr})
            assert run.returncode == 0 and token not in run.stdout and token not in run.stderr
        listed = subprocess.run([str(binary),"plugin","list"], cwd=workspace, env=env,
                                capture_output=True, text=True, check=True, timeout=30)
        assert "github@agents-public-github" in listed.stdout
        result["plugin_list"] = {"stdout":listed.stdout,"stderr":listed.stderr}
        installed_config = (home / "config.json").read_bytes(); result["installed_config_sha256"] = sha(home / "config.json")
        client = Client([str(binary),"--acp","--disable-builtin-mcps","--no-auto-update","--no-remote"], workspace, env, "deny", "")
        deadline = time.monotonic() + 60
        client.response(client.request("initialize", {"protocolVersion":1,"clientCapabilities":{}}), deadline)
        started = client.response(client.request("session/new", {"cwd":str(workspace),"mcpServers":[]}), deadline)
        result["session"] = started
        result["completion"] = client.response(client.request("session/prompt", {"sessionId":started["sessionId"],
            "prompt":[{"type":"text","text":"Reply that the public GitHub discovery test is ready. Do not call tools."}]}), deadline)
        result.update(events=client.events, approvals=client.approvals, stderr="".join(client.errors),
                      model_requests=requests, provider_errors=errors)
        direct = json.loads((repo / "WORKBENCH/evidence/native-draft2-debug/public-github-mcp-readonly-probe.json").read_text())
        expected = direct["tool_names"]
        observed = sorted({tool["function"]["name"][len("github-"):] for request in requests
                           for tool in request.get("tools", []) if tool.get("type") == "function"
                           and tool.get("function", {}).get("name", "").startswith("github-")})
        result.update(github_tool_count=len(observed), github_tool_names=observed,
                      direct_tool_count=len(expected), direct_tool_names_match=observed == expected)
        assert observed == expected and len(observed) == 47 and "get_me" in observed
        assert result["completion"]["stopReason"] == "end_turn"
        assert any("public GitHub discovery test" in json.dumps(request.get("messages", [])) for request in requests)
        native_config = (home / "config.json").read_text()
        parsed_config = json.loads("\n".join(line for line in native_config.splitlines() if not line.lstrip().startswith("//")))
        result.update(native_config_changed=native_config.encode() != installed_config,
                      native_trusted_folders=parsed_config.get("trustedFolders", []),
                      native_first_launch_recorded="firstLaunchAt" in parsed_config)
        assert not client.approvals and not errors
        assert parsed_config.get("trustedFolders") == [str(workspace)] and token not in native_config
        assert not any("tool_call" in json.dumps(event).lower() or "mcpToolCall" in json.dumps(event) for event in client.events)
        result["passed"] = True
    except Exception as error:
        result.update(passed=False, error=type(error).__name__ + ": " + str(error), traceback=traceback.format_exc())
    finally:
        if client:
            client.close(); result.setdefault("events", client.events); result.setdefault("approvals", client.approvals); result.setdefault("stderr", "".join(client.errors))
        server.shutdown(); server.server_close()
        serialized = json.dumps(result, indent=2)
        if token in serialized:
            result = {"passed":False,"error":"credential appeared in evidence payload","credential_value_stored":False,
                      "credential_sha256_stored":False,"remote_mutations":False}; serialized = json.dumps(result, indent=2)
        token = ""; output.write_text(serialized + "\n")
    print(json.dumps({"passed":result["passed"],"github_tool_count":result.get("github_tool_count"),"error":result.get("error")}))
    return 0 if result["passed"] else 1


if __name__ == "__main__": raise SystemExit(main())
