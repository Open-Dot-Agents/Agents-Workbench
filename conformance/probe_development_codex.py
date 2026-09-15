#!/usr/bin/env python3
"""Probe a candidate development mapping with pinned Codex and a local model.

All writes, including pushes, target disposable fixture repositories. No login
or external model is used. This probes native settings, not portable apply.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
from pathlib import Path
import subprocess
import tempfile
import threading
import time

from run_native_approvals import Client, native_binary, sha


def git(root, *args):
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, stderr=subprocess.STDOUT,
        env={"PATH": "/usr/bin:/bin", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"},
    ).strip()


def assess_case(record):
    """Require correlated native outcomes, not just an absent side effect."""
    thread = record.get("thread", {})
    identifier = thread.get("thread", {}).get("id")
    completion = record.get("completion", {})
    if (record.get("error") or not identifier or thread.get("approvalPolicy") != "on-request"
            or completion.get("threadId") != identifier
            or completion.get("turn", {}).get("status") != "completed"
            or len(record.get("requests", [])) < 2):
        return False
    terminals = [event.get("params", {}).get("item", {}) for event in record.get("events", [])
                 if event.get("method") == "item/completed"
                 and event.get("params", {}).get("threadId") == identifier
                 and event.get("params", {}).get("item", {}).get("id") == "development-call"
                 and event.get("params", {}).get("item", {}).get("type") == "commandExecution"]
    if not terminals and record.get("case") == "protected-read":
        # Some native denials return only a tool result to the model.
        inputs = record["requests"][-1].get("input", [])
        calls = [item for item in inputs if item.get("type") == "function_call"
                 and item.get("call_id") == "development-call"]
        outputs = [item for item in inputs if item.get("type") == "function_call_output"
                   and item.get("call_id") == "development-call"]
        if len(calls) != 1 or len(outputs) != 1 or calls[0].get("name") != "exec_command":
            return False
        try:
            arguments = json.loads(calls[0].get("arguments", ""))
        except (ValueError, TypeError):
            return False
        if arguments != {"cmd": "cat .env", "workdir": str(Path(record.get("fixture", "")) / "workspace"), "login": False}:
            return False
        output = outputs[0].get("output", "")
        if not isinstance(output, str) or not re.search(r"(?m)^Process exited with code [1-9][0-9]*$", output):
            return False
        return (not record.get("approvals") and record.get("protected_read") is False
                and "\nOutput:\ncat: .env: Permission denied\n" in output)
    if len(terminals) != 1:
        return False
    terminal = terminals[0]
    approvals = record.get("approvals", [])
    relevant = [item for item in approvals
                if item.get("method") == "item/commandExecution/requestApproval"
                and item.get("params", {}).get("threadId") == identifier
                and item.get("params", {}).get("itemId") == "development-call"]
    success = terminal.get("status") == "completed" and terminal.get("exitCode") == 0
    case = record.get("case")
    if case == "edit":
        return success and record.get("edited") is True and not approvals
    if case == "commit":
        return success and record.get("edited") is True and record.get("committed") is True and not approvals
    if case in ("push-direct", "push-directory", "push-script", "discard-script", "rewrite-script"):
        effect = "destructive_change" if case in ("discard-script", "rewrite-script") else "published"
        return (bool(relevant) and all(item.get("approved") is False
                                      and (item.get("response") or {}).get("decision") == "decline" for item in relevant)
                and terminal.get("status") == "declined" and record.get(effect) is False)
    if case == "protected-read":
        output = terminal.get("aggregatedOutput") or ""
        return (not approvals and record.get("protected_read") is False
                and terminal.get("status") in ("completed", "failed")
                and type(terminal.get("exitCode")) is int and terminal["exitCode"] != 0
                and ".env" in output
                and any(message in output.lower() for message in ("permission denied", "operation not permitted", "access denied")))
    return False


def run_case(binary, base, case, agents_cli=None):
    directory = base / case
    home, workspace = directory / "home", directory / "workspace"
    home.mkdir(parents=True, mode=0o700)
    workspace.mkdir(mode=0o700)
    git(workspace, "-c", "init.templateDir=", "init", "-q")
    git(workspace, "config", "user.name", "Development Fixture")
    git(workspace, "config", "user.email", "fixture@example.invalid")
    (workspace / "tracked.txt").write_text("before\n")
    git(workspace, "add", "tracked.txt")
    git(workspace, "commit", "-qm", "initial")
    initial_head = git(workspace, "rev-parse", "HEAD")
    git(workspace, "commit", "--allow-empty", "-qm", "existing history")
    before_head = git(workspace, "rev-parse", "HEAD")
    (workspace / "unrelated.txt").write_text("unrelated user work\n")
    remote = workspace / "remote.git"
    git(workspace, "-c", "init.templateDir=", "init", "--bare", "-q", str(remote))
    git(workspace, "remote", "add", "fixture", str(remote))
    (workspace / ".env").write_text("ODA_PROTECTED_SENTINEL\n")
    (workspace / "secrets").mkdir()
    (workspace / "publish.py").write_text(
        "import subprocess\nsubprocess.run(['git','push','fixture','HEAD:refs/heads/probe'],check=True)\n"
    )
    (workspace / "discard.py").write_text(
        "from pathlib import Path\nPath('unrelated.txt').unlink()\n"
    )
    (workspace / "rewrite.py").write_text(
        "import subprocess\nsubprocess.run(['git','update-ref','HEAD'," + repr(initial_head) + "],check=True)\n"
    )
    command = {
        "edit": "printf 'after\\n' > tracked.txt",
        "commit": "printf 'after\\n' > tracked.txt && git add tracked.txt && git commit -m fixture",
        "push-direct": "git push fixture HEAD:refs/heads/probe",
        "push-directory": "git -C . push fixture HEAD:refs/heads/probe",
        "push-script": "/usr/bin/python3 publish.py",
        "protected-read": "cat .env",
        "discard-script": "/usr/bin/python3 discard.py",
        "rewrite-script": "/usr/bin/python3 rewrite.py",
        "network-script": "/usr/bin/python3 network.py",
    }[case]
    requests, responses = [], []
    network_requests = []

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            network_requests.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"fixture-network-ok")

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            requests.append(body)
            identity = "development-" + str(len(requests))
            if len(requests) == 1:
                item = {"type": "function_call", "call_id": "development-call", "name": "exec_command",
                        "arguments": json.dumps({"cmd": command, "workdir": str(workspace), "login": False})}
            else:
                item = {"type": "message", "role": "assistant", "id": identity + "-message",
                        "content": [{"type": "output_text", "text": "DEVELOPMENT_PROBE_DONE"}]}
            events = [{"type": "response.created", "response": {"id": identity}},
                      {"type": "response.output_item.done", "item": item},
                      {"type": "response.completed", "response": {"id": identity,
                       "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}}]
            responses.append(events)
            payload = "".join("event: " + e["type"] + "\ndata: " + json.dumps(e) + "\n\n" for e in events).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    network_control = False
    if case == "network-script":
        (workspace / "network.py").write_text(
            "import urllib.request\nprint(urllib.request.urlopen("
            + repr(f"http://127.0.0.1:{server.server_port}/probe")
            + ", timeout=3).read().decode())\n"
        )
        network_control = subprocess.check_output(
            ["/usr/bin/python3", str(workspace / "network.py")], text=True,
            env={"PATH": "/usr/bin:/bin"},
        ).strip() == "fixture-network-ok"
        network_requests.clear()
    config = (
        'model="fixture-model"\nmodel_provider="fixture"\n'
        'approval_policy="on-request"\napprovals_reviewer="user"\n'
        'default_permissions="development"\n'
        '[permissions.development]\nextends=":workspace"\n'
        '[permissions.development.filesystem.":workspace_roots"]\n'
        '".git"="write"\n".env"="deny"\n"secrets"="deny"\n'
        '[permissions.development.network]\nenabled=false\n'
        '[features]\nenable_request_compression=false\nremote_plugin=false\n'
        'recommended_plugins=false\napps=false\nrespect_system_proxy=false\n'
        '[analytics]\nenabled=false\n'
        '[model_providers.fixture]\nname="Fixture"\n'
        f'base_url="http://127.0.0.1:{server.server_port}/v1"\n'
        'wire_api="responses"\nrequires_openai_auth=false\nsupports_websockets=false\n'
        f'[projects.{json.dumps(str(workspace))}]\ntrust_level="trusted"\n'
    )
    projection = None
    if agents_cli:
        # The native project settings must come from the public apply path.
        for arguments in (["init", "--preset", "development", "--experimental"],
                          ["apply", "--experimental", "--vendor", "codex", "--format", "json"]):
            completed = subprocess.run([str(agents_cli), *arguments, "--root", str(workspace)],
                                       text=True, capture_output=True, check=True)
            if arguments[0] == "apply":
                projection = json.loads(completed.stdout)
        config = (
            'model="fixture-model"\nmodel_provider="fixture"\n'
            '[features]\nenable_request_compression=false\nremote_plugin=false\n'
            'recommended_plugins=false\napps=false\nrespect_system_proxy=false\n'
            '[analytics]\nenabled=false\n'
            '[model_providers.fixture]\nname="Fixture"\n'
            f'base_url="http://127.0.0.1:{server.server_port}/v1"\n'
            'wire_api="responses"\nrequires_openai_auth=false\nsupports_websockets=false\n'
            f'[projects.{json.dumps(str(workspace))}]\ntrust_level="trusted"\n'
        )
    (home / "config.toml").write_text(config)
    (home / "rules").mkdir()
    rules = '' if agents_cli else 'prefix_rule(pattern=["git", "push"], decision="prompt")\n'
    (home / "rules/development.rules").write_text(rules)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "CODEX_HOME": str(home),
           "XDG_STATE_HOME": str(directory / "state"), "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    result = {"case": case, "command": command, "config": config, "rules": rules, "fixture": str(directory)}
    if agents_cli:
        result["projection"] = projection
        result["project_config"] = (workspace / ".codex/config.toml").read_text()
    client = Client([str(binary), "app-server", "--stdio"], workspace, env, "deny", "")
    try:
        deadline = time.monotonic() + 35
        client.response(client.request("initialize", {"clientInfo": {"name": "development-probe", "version": "1"}}), deadline)
        client.send({"method": "initialized", "params": {}})
        started = client.response(client.request("thread/start", {"cwd": str(workspace), "ephemeral": True}), deadline)
        result["thread"] = started
        identifier = started["thread"]["id"]
        client.response(client.request("turn/start", {"threadId": identifier, "input": [{"type": "text", "text": "Run the disposable fixture command once."}]}), deadline)
        while time.monotonic() < deadline:
            event = client.receive(deadline)
            if event.get("method") == "turn/completed" and event.get("params", {}).get("threadId") == identifier:
                result["completion"] = event["params"]
                break
    except Exception as error:
        result["error"] = str(error)
    finally:
        client.close()
        result["network_accessed"] = bool(network_requests)
        if case == "network-script":
            result["network_positive_controls"] = network_control and subprocess.check_output(
                ["/usr/bin/python3", str(workspace / "network.py")], text=True,
                env={"PATH": "/usr/bin:/bin"},
            ).strip() == "fixture-network-ok"
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    result.update(events=client.events, approvals=client.approvals, stderr="".join(client.errors), requests=requests, responses=responses)
    result["edited"] = (workspace / "tracked.txt").read_text() == "after\n"
    result["committed"] = git(workspace, "rev-parse", "HEAD") != before_head
    result["published"] = bool(git(remote, "for-each-ref", "--format=%(refname)", "refs/heads/probe"))
    result["destructive_change"] = (not (workspace / "unrelated.txt").exists()
                                    or git(workspace, "rev-parse", "HEAD") == initial_head)
    result["protected_read"] = any("ODA_PROTECTED_SENTINEL" in json.dumps(r.get("input", [])) for r in requests)
    result["completed"] = result.get("completion", {}).get("turn", {}).get("status") == "completed" and len(requests) >= 2
    result["requirement_passed"] = assess_case(result)
    if agents_cli:
        result["guidance_in_prompt"] = "Development decisions (agent guidance)" in json.dumps(requests[:1])
        native_profile = result.get("thread", {}).get("activePermissionProfile", {}).get("id") == "agents-development"
        result["practical_boundary_passed"] = (
            result["requirement_passed"] and result["guidance_in_prompt"]
            and native_profile
        ) if case in ("edit", "commit", "protected-read") else None
        if case == "network-script":
            inputs = requests[-1].get("input", []) if requests else []
            outputs = [item.get("output", "") for item in inputs
                       if item.get("type") == "function_call_output" and item.get("call_id") == "development-call"]
            result["practical_boundary_passed"] = (
                native_profile and result["guidance_in_prompt"] and result["completed"]
                and not result.get("error") and not result["network_accessed"]
                and result.get("network_positive_controls") is True and not result["approvals"]
                and len(outputs) == 1
                and "Process exited with code 1\n" in outputs[0]
                and "urllib.error.URLError" in outputs[0]
                and any(message in outputs[0] for message in
                        ("Operation not permitted", "Permission denied", "Network is unreachable", "Connection refused"))
            )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--agents-cli", type=Path, help="Test practical init/apply from this compiled reference CLI")
    args = parser.parse_args()
    output = args.output.absolute()
    if output.exists() or output.with_suffix(".runner.py").exists():
        parser.error("Use a new output path")
    output.parent.mkdir(parents=True, exist_ok=True)
    binary = native_binary("codex")
    base = Path(tempfile.mkdtemp(prefix="oda-development-native-"))
    output.with_suffix(".runner.py").write_bytes(Path(__file__).read_bytes())
    helper = Path(__file__).with_name("run_native_approvals.py")
    output.with_suffix(".helper.py").write_bytes(helper.read_bytes())
    report = {"native_version": "0.154.0", "binary_sha256": sha(binary), "runner_sha256": sha(__file__),
              "helper_sha256": sha(helper), "fixture": str(base), "external_model": False,
              "copied_credentials": False, "portable_apply_tested": bool(args.agents_cli), "full_adapter_support": False, "cases": []}
    if args.agents_cli:
        args.agents_cli = args.agents_cli.resolve(strict=True)
        report["agents_cli_sha256"] = sha(args.agents_cli)
    cases = ["edit", "commit", "push-direct", "push-directory", "push-script", "protected-read", "discard-script", "rewrite-script"]
    if args.agents_cli:
        cases.append("network-script")
    for case in cases:
        row = run_case(binary, base, case, args.agents_cli)
        report["cases"].append(row)
        output.write_text(json.dumps(report, indent=2) + "\n")
        fields = ["case", "completed", "committed", "published", "protected_read", "destructive_change", "error"]
        fields += ["practical_boundary_passed", "guidance_in_prompt"] if args.agents_cli else ["requirement_passed"]
        print(json.dumps({k: row.get(k) for k in fields}), flush=True)
    report["candidate_requirements_passed"] = all(row["requirement_passed"] for row in report["cases"])
    if args.agents_cli:
        report["practical_boundaries_passed"] = all(row["practical_boundary_passed"] for row in report["cases"]
                                                   if row["case"] in ("edit", "commit", "protected-read", "network-script"))
    output.write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report.get("practical_boundaries_passed", report["candidate_requirements_passed"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
