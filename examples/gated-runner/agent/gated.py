"""Run a command through the gated runner, the only way this agent can act.

    from gated import run, write
    run("ls", ["-la"])                       # -> the runner's answer
    write("notes.md", "# Notes\\n")

Each call: ask the runner what the command needs (/v1/plan), ask Archytan
Lite for a capability for each path (/v1/authorize, with this agent's own
credential), then hand the command and the capabilities to the runner
(/v1/run), which spends them and only then runs it.

A path the policy holds for a person's approval (Archytan Lite 2.9.0) is
waited for: see capabilities_for. Nothing runs until every path is allowed.

Python's standard library only.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime

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


def get(url, token=None, timeout=10):
    """GET JSON and return (status, parsed body). Network errors are status 0."""
    req = urllib.request.Request(url, method="GET")
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
    except (urllib.error.URLError, OSError, ValueError) as e:
        return 0, {"error": str(e)}


def _seconds_env(name, default):
    """A number of seconds from the environment; unreadable or negative is 0."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        return 0.0
    return value if value >= 0 else 0.0


def hold_wait_seconds():
    """Longest this agent waits for a person to decide a hold, in seconds.

    HOLD_WAIT_SECONDS, default 300, since a person needs minutes. 0 means
    don't wait: a held action is refused at once. A hold's own expiry can
    end the wait sooner.
    """
    return _seconds_env("HOLD_WAIT_SECONDS", 300.0)


def hold_poll_seconds():
    """How often open holds are read, in seconds (HOLD_POLL_SECONDS, default 2)."""
    return max(_seconds_env("HOLD_POLL_SECONDS", 2.0), 0.05)


def _ask(action, resource_id, key=None):
    """Like ask, but also returns the exact request sent, so a held path can be replayed."""
    request = {
        "action": action,
        "actor": ACTOR,
        "resource": {"type": "path", "id": resource_id},
        "idempotency_key": key or str(uuid.uuid4()),
    }
    status, body = post(GATE + "/v1/authorize", request, token=_token())
    token = None
    if status == 200 and body.get("decision") == "ALLOW":
        # A gate without ARCHYTAN_LITE_CAPABILITIES=on allows but issues none.
        token = (body.get("capability") or {}).get("token")
    return token, status, body, request


def ask(action, resource_id):
    """Ask Archytan Lite for one capability. Returns (token or None, status, body)."""
    return _ask(action, resource_id)[:3]


def hold_id(status, body):
    """The hold's id if this answer is a hold, else None.

    A hold is a 202 whose decision is HOLD, whose hold names an id, and whose
    receipt (a HOLD with that same id) is present. This example doesn't verify
    receipt signatures, so it is strict about the shape instead: any other 202
    is not a hold, and no 202 is ever an allow.
    """
    if status != 202 or not isinstance(body, dict) or body.get("decision") != "HOLD":
        return None
    hold = body.get("hold")
    receipt = body.get("receipt")
    if not isinstance(hold, dict) or not isinstance(receipt, dict):
        return None
    hid = hold.get("hold_id")
    if not isinstance(hid, str) or not hid or receipt.get("receipt_id") != hid or receipt.get("decision") != "HOLD":
        return None
    return hid


def _parse_time(text):
    """An RFC 3339 time as epoch seconds, or None if it can't be read."""
    if not isinstance(text, str):
        return None
    m = re.fullmatch(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)", text)
    if not m:
        return None
    zone = "+00:00" if m.group(3) == "Z" else m.group(3)
    frac = "." + m.group(2)[:6].ljust(6, "0") if m.group(2) else ""
    try:
        return datetime.fromisoformat(m.group(1) + frac + zone).timestamp()
    except ValueError:
        return None


# Seconds past a hold's expires_at that the wait goes on, for clock skew.
HOLD_GRACE = 2.0
HOLD_STATES = ("open", "approved", "denied", "expired", "refused")


def wait_for_holds(holds):
    """Wait until none of the holds is open, or the deadline passes.

    holds maps hold_id to its expires_at. Returns {hold_id: state}; a hold
    still open at the deadline stays "open", and one that can't be read for
    good is "unreadable". The caller refuses anything that isn't "approved".
    The deadline is the earliest expires_at plus HOLD_GRACE, capped by
    HOLD_WAIT_SECONDS from now. Reading a hold mints nothing.
    """
    states = {hid: "open" for hid in holds}
    wait = hold_wait_seconds()
    if wait <= 0:
        return states
    deadline = time.time() + wait
    expiries = [_parse_time(e) for e in holds.values()]
    if all(t is not None for t in expiries):
        deadline = min(deadline, min(expiries) + HOLD_GRACE)
    while True:
        for hid in [h for h, st in states.items() if st == "open"]:
            status, view = get(f"{GATE}/v1/holds/{hid}", token=_token())
            state = view.get("state") if status == 200 and isinstance(view, dict) else None
            if state in HOLD_STATES:
                states[hid] = state
            elif status == 200 or status in (401, 403, 404):
                # A state this code doesn't know, or one the gate won't show
                # this caller: waiting longer won't change that.
                states[hid] = "unreadable"
            # Anything else (no answer, 429, 5xx) is tried again until the deadline.
        if all(st != "open" for st in states.values()) or time.time() >= deadline:
            return states
        time.sleep(max(min(hold_poll_seconds(), deadline - time.time()), 0))


def authorize(action, resource_id):
    """Ask Archytan Lite for one capability. Returns the token, or None."""
    return ask(action, resource_id)[0]


def plan(request):
    return post(RUNNER + "/v1/plan", request)


def _hold_refusal(hid, state, collect_status=None):
    """The (status, body) capabilities_for reports for a hold that didn't end in a capability."""
    body = {"decision": "HOLD", "hold_id": hid, "hold_state": state}
    if collect_status is not None:
        body["collect_status"] = collect_status
    return 202, body


def capabilities_for(p):
    """Every capability a plan needs: (caps, None), or (None, (path, status, body)).

    Every path is asked first. If the policy holds any of them for a person,
    this waits for all the holds (wait_for_holds) and then:
      - if any hold ended other than approved, or wasn't answered in time,
        returns a refusal for that path (status 202, body with hold_state)
        and nothing is collected;
      - otherwise re-asks every path that wasn't held, with new keys, and
        only then collects each held path by replaying its exact request
        (same body, same idempotency key). A capability lives 30 seconds
        from when it is minted, and the wait may have used that up for the
        ones asked before it; re-asking all of them is simpler than
        guessing which have expired. The held ones are collected last, so
        their 30 seconds start just before the command runs.
    """
    caps = {}
    held = {}  # path -> (hold_id, expires_at, request)
    for rid in p["resources"]:
        tok, status, body, request = _ask(p["action"], rid)
        hid = hold_id(status, body)
        if hid is not None:
            held[rid] = (hid, body["hold"].get("expires_at"), request)
        elif tok is None:
            # Holds already opened for earlier paths stay open for their
            # approvers; this command won't run, and nothing collects them.
            return None, (rid, status, body)
        else:
            caps[rid] = tok
    if not held:
        return caps, None

    states = wait_for_holds({hid: expires for hid, expires, _ in held.values()})
    for rid, (hid, _, _) in held.items():
        if states[hid] != "approved":
            return None, (rid, *_hold_refusal(hid, states[hid]))

    for rid in list(caps):
        tok, status, body, _ = _ask(p["action"], rid)
        hid = hold_id(status, body)
        if hid is not None:
            # Policy changed while waiting; a new hold needs its own wait.
            return None, (rid, *_hold_refusal(hid, "held_again"))
        if tok is None:
            return None, (rid, status, body)
        caps[rid] = tok

    for rid, (hid, _, request) in held.items():
        status, body = post(GATE + "/v1/authorize", request, token=_token())
        tok = (body.get("capability") or {}).get("token") if status == 200 and body.get("decision") == "ALLOW" else None
        # The 200 must carry the HOLD receipt this path started from.
        receipt = body.get("hold_receipt")
        if tok is None or not isinstance(receipt, dict) or receipt.get("receipt_id") != hid:
            return None, (rid, *_hold_refusal(hid, "approved", collect_status=status))
        caps[rid] = tok
    return caps, None


def _hold_reason(action, rid, body):
    """Why a 202 ended in a refusal: a decision was made (or is still waiting), unlike a gate that didn't answer."""
    state = body.get("hold_state")
    if state is None:
        return f"Archytan Lite answered 202 for {action} on {rid} without a valid hold, so it is treated as a refusal"
    held = f"Archytan Lite held {action} on {rid} for a person's approval (hold {body.get('hold_id')})"
    if state == "open":
        if hold_wait_seconds() <= 0:
            return held + ", and HOLD_WAIT_SECONDS is 0, so this agent doesn't wait"
        return held + f", and nobody answered within {hold_wait_seconds():g} seconds (or before the hold expired)"
    if state == "approved":
        return held + f", and approved it, but the approval couldn't be collected (gate answered {body.get('collect_status')})"
    if state == "held_again":
        return held + " again after the wait, so the command was not run"
    return held + f", and the hold ended {state}"


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
        if status == 202:
            return 403, {"decision": "BLOCK", "reason": _hold_reason(p["action"], rid, body), **p}
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
