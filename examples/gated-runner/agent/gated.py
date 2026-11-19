"""Run a command through the gated runner, the only way this agent can act.

    from gated import run, write
    run("ls", ["-la"])                       # -> the runner's answer
    write("notes.md", "# Notes\\n")

Each call: ask the runner what the command needs (/v1/plan), ask Archytan
Lite for a capability for each path (/v1/authorize, with this agent's own
credential), then hand the command and the capabilities to the runner
(/v1/run), which spends them and only then runs it.

Python's standard library only.
"""

import json
import os
import sys
import urllib.error
import urllib.request
import uuid

GATE = os.environ.get("GATE_URL", "http://gate:8421")
RUNNER = os.environ.get("RUNNER_URL", "http://runner:8431")
ACTOR = {"uid": "agent", "role": "assistant"}


def _token():
    with open(os.environ.get("AGENT_TOKEN_PATH", "/run/secrets/agent_token")) as f:
        return f.read().strip()


def post(url, body, token=None, raw=None, timeout=90):
    """POST JSON and return (status, parsed body). Network errors are status 0."""
    data = raw if raw is not None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, OSError) as e:
        return 0, {"error": str(e)}


def ask(action, resource_id):
    """Ask Archytan Lite for one capability. Returns (token or None, status, body)."""
    status, body = post(GATE + "/v1/authorize", {
        "action": action,
        "actor": ACTOR,
        "resource": {"type": "path", "id": resource_id},
        "idempotency_key": str(uuid.uuid4()),
    }, token=_token())
    token = None
    if status == 200 and body.get("decision") == "ALLOW":
        # A gate without ARCHYTAN_LITE_CAPABILITIES=on allows but issues none.
        token = (body.get("capability") or {}).get("token")
    return token, status, body


def authorize(action, resource_id):
    """Ask Archytan Lite for one capability. Returns the token, or None."""
    return ask(action, resource_id)[0]


def plan(request):
    return post(RUNNER + "/v1/plan", request)


def capabilities_for(p):
    """Every capability a plan needs: (caps, None), or (None, (path, status, body))."""
    caps = {}
    for rid in p["resources"]:
        tok, status, body = ask(p["action"], rid)
        if tok is None:
            return None, (rid, status, body)
        caps[rid] = tok
    return caps, None


def gated(request):
    """Plan, authorize, run. Returns (status, body)."""
    status, p = plan(request)
    if status != 200:
        return status, p
    if p.get("refused"):
        return 403, {"decision": "BLOCK", "reason": p["refused"], **p}
    caps, refused = capabilities_for(p)
    if caps is None:
        rid, status, body = refused
        if status == 0:
            # The gate didn't answer: nothing was decided, and nothing runs.
            return 0, {"error": f"Archytan Lite didn't answer: {body.get('error', '')}", **p}
        if status == 200 and body.get("decision") == "ALLOW":
            return 403, {"decision": "BLOCK", "reason": f"Archytan Lite allowed {p['action']} on {rid} but issued no capability (is ARCHYTAN_LITE_CAPABILITIES on?)", **p}
        if (status == 200 and body.get("decision") == "BLOCK") or status == 403:
            # The gate answers a bad credential and a policy refusal alike;
            # its log has the reason under this trace id.
            trace = f" (gate trace {body['trace_id']})" if body.get("trace_id") else ""
            return 403, {"decision": "BLOCK", "reason": f"Archytan Lite refused {p['action']} on {rid}{trace}", **p}
        # Rate-limited (429), a server error, or a request the gate couldn't
        # read: no decision was made, so it isn't reported as a refusal.
        err = body.get("error") or body.get("reason") or ""
        return status, {"error": f"Archytan Lite answered {status} for {rid}: {err}", **p}
    # The runner answers a run within its longest run plus 30 seconds (285 s
    # with the defaults); waiting less could give up after the capabilities
    # are spent.
    return post(RUNNER + "/v1/run", {**request, "capabilities": caps}, timeout=300)


def run(program, args=(), cwd=None, stdin=None):
    cmd = {"program": program, "args": list(args)}
    if cwd:
        cmd["cwd"] = cwd
    req = {"command": cmd}
    if stdin is not None:
        req["stdin"] = stdin
    return gated(req)


def write(path, content):
    return gated({"write": {"path": path, "content": content}})


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: gated.py PROGRAM [ARG...]")
    status, body = run(sys.argv[1], sys.argv[2:])
    result = body.get("result") or {}
    sys.stdout.write(result.get("stdout", ""))
    sys.stderr.write(result.get("stderr", ""))
    if body.get("decision") != "ALLOW":
        print(f"[{body.get('decision', status)}] {body.get('reason', body.get('error', ''))}", file=sys.stderr)
        sys.exit(1)
    if result.get("error") or status != 200:
        # The capabilities were spent, but the sandbox couldn't do it.
        print(f"[sandbox] {result.get('error') or body.get('reason', '')}", file=sys.stderr)
        sys.exit(1)
    sys.exit(result.get("exit_code", 0))
