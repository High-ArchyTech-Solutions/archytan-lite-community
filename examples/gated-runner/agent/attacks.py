"""Try every way around the gate, from where the agent sits.

    python attacks.py               the attacks that need nothing else
    python attacks.py gate-down-1   get capabilities, then the host stops the gate
    python attacks.py gate-down-2   spend them while the gate is down

Each attempt that gets through is a FAIL. Attempts that would leave a trace
in the workspace leave a file whose name starts with "canary-": the host
checks afterwards, from the sandbox itself, that none exists.
"""

import json
import os
import socket
import sys
import time

import gated

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f": {detail}"), flush=True)


def refused(status, body):
    return status in (403, 503) and body.get("decision") == "BLOCK"


def cannot_connect(host, port, timeout=3):
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return False
    except OSError:
        return True


def connect_outcome(host, port, timeout=3):
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return "connected"
    except ConnectionRefusedError:
        return "refused"
    except OSError:
        return "no answer"


def cannot_resolve(host):
    try:
        socket.getaddrinfo(host, None)
        return False
    except OSError:
        return True


def environment():
    """No other route: nothing to reach but the gate and the runner."""
    check("no Docker socket", not os.path.exists("/var/run/docker.sock") and not os.path.exists("/run/docker.sock"))
    check("no workspace mounted here", not os.path.exists("/work"))
    check("no executor socket here", not os.path.exists("/run/archytan-runner"))
    check("the sandbox has no address", cannot_resolve("sandbox"))
    # The host's own address on the agents network (its gateway) is the one
    # host address the agent can reach. test-attacks.sh passes it in, and
    # a listener it published on all of the host's addresses.
    gateway = os.environ.get("HOST_GATEWAY", "")
    check("the host's address is known to the test", gateway != "", "HOST_GATEWAY not set")
    if gateway:
        # Whether the address answers at all: if a firewall drops everything
        # sent to it, the checks below pass without testing much.
        answer = connect_outcome(gateway, 1)
        if answer == "refused":
            print("PASS the host's address answers: a closed port on it refuses at once", flush=True)
        else:
            print(f"NOTE a closed port on the host's address gave {answer!r}, so the checks on it prove less", flush=True)
        check("no Docker API on the host's address", cannot_connect(gateway, 2375) and cannot_connect(gateway, 2376))
        port = os.environ.get("HOST_PUBLISHED_PORT", "")
        check("no way to a port the host publishes", port != "" and cannot_connect(gateway, int(port)), f"port {port!r}")
        # A service on the host's own network isn't forwarded the way a
        # published port is, so the agent can reach it: what the README says
        # to keep off the host's addresses.
        svc = os.environ.get("HOST_SERVICE_PORT", "")
        if svc:
            reach = connect_outcome(gateway, int(svc))
            if reach == "connected":
                print("NOTE a service on the host's own network is reachable here: keep the Docker API and other services off it", flush=True)
            else:
                print(f"PASS a service on the host's own network isn't reachable here ({reach})", flush=True)
    check("no Docker API by the host's name", cannot_connect("host.docker.internal", 2375) and cannot_connect("host.docker.internal", 2376))
    check("no internet", cannot_connect("1.1.1.1", 443) and cannot_connect("8.8.8.8", 53))


def through_the_gate():
    status, body = gated.write("hello.txt", "hello from the agent\n")
    check("an allowed write goes through", status == 200 and body.get("decision") == "ALLOW", body)
    status, body = gated.run("cat", ["hello.txt"])
    check("an allowed read goes through", status == 200 and (body.get("result") or {}).get("stdout") == "hello from the agent\n", body)


def without_capabilities():
    status, body = gated.post(gated.RUNNER + "/v1/run", {"command": {"program": "touch", "args": ["canary-nocap"]}})
    check("no capabilities: refused", refused(status, body), body)
    status, body = gated.post(gated.RUNNER + "/v1/run", {"write": {"path": "canary-nocap-write", "content": "x"}})
    check("a write without capabilities: refused", refused(status, body), body)


def replay():
    req = {"command": {"program": "touch", "args": ["replay-once"]}}
    _, p = gated.plan(req)
    caps, _ = gated.capabilities_for(p)
    first, _ = gated.post(gated.RUNNER + "/v1/run", {**req, "capabilities": caps})
    req2 = {"command": {"program": "touch", "args": ["replay-once"]}, "capabilities": caps}
    status, body = gated.post(gated.RUNNER + "/v1/run", req2)
    check("replayed capabilities: refused", first == 200 and refused(status, body), (first, body))


def issued(caps):
    # Every capability was really issued, so a refusal comes from spending
    # it, not from one that was never there.
    return bool(caps) and all(caps.values())


def wrong_path():
    caps = {"/work": gated.authorize("file.write", "/work"), "/work/canary-wrongpath": gated.authorize("file.write", "/work/allowed")}
    status, body = gated.post(gated.RUNNER + "/v1/run", {"command": {"program": "touch", "args": ["canary-wrongpath"]}, "capabilities": caps})
    check("a capability for another path: refused", issued(caps) and refused(status, body) and not body.get("missing"), body)


def wrong_action():
    caps = {"/work": gated.authorize("file.read", "/work"), "/work/hello.txt": gated.authorize("file.read", "/work/hello.txt")}
    status, body = gated.post(gated.RUNNER + "/v1/run", {"command": {"program": "rm", "args": ["hello.txt"]}, "capabilities": caps})
    check("read capabilities spent on rm: refused",
          issued(caps) and refused(status, body) and not body.get("missing") and body.get("action") == "file.delete", body)


def glued_path():
    # "cp -tDIR" names DIR glued to the option: it must be in the plan, and
    # a run without a capability for it is refused.
    req = {"command": {"program": "cp", "args": ["-tcanary-glued", "hello.txt"]}}
    _, p = gated.plan(req)
    planned = "/work/canary-glued" in (p.get("resources") or [])
    caps = {"/work": gated.authorize("file.write", "/work"), "/work/hello.txt": gated.authorize("file.write", "/work/hello.txt")}
    status, body = gated.post(gated.RUNNER + "/v1/run", {**req, "capabilities": caps})
    check("a folder glued to an option (cp -tDIR) needs its own capability",
          planned and issued(caps) and refused(status, body) and "/work/canary-glued" in (body.get("missing") or []), (p, body))


def policy_refusals():
    check("the gate won't grant file.delete to the agent", gated.authorize("file.delete", "/work/hello.txt") is None)
    # The gate answers a bad credential and a policy refusal with the same
    # 403, so the client passes on the gate's trace id for its log.
    status, body = gated.run("rm", ["hello.txt"])
    check("a refusal from the gate carries its trace id",
          refused(status, body) and "(gate trace trc_" in body.get("reason", ""), body)
    status, body = gated.run("python3", ["-c", "open('/work/canary-inline','w')"])
    check("inline code: refused", refused(status, body), body)
    status, body = gated.run("curl", ["example.com"])
    check("network programs: refused", refused(status, body), body)


def forged():
    caps = {"/work": "v1.Zm9yZ2Vk.Zm9yZ2Vk", "/work/canary-forged": "forged"}
    status, body = gated.post(gated.RUNNER + "/v1/run", {"command": {"program": "touch", "args": ["canary-forged"]}, "capabilities": caps})
    check("forged capabilities: refused", refused(status, body), body)


def spent_by_agent():
    req = {"command": {"program": "touch", "args": ["canary-agent-spent"]}}
    _, p = gated.plan(req)
    caps, _ = gated.capabilities_for(p)
    spent = issued(caps)
    for rid, tok in (caps or {}).items():
        s, _ = gated.post(gated.GATE + "/v1/redeem", {"token": tok, "action": p["action"], "resource_type": "path", "resource_id": rid}, token=gated._token())
        spent = spent and s == 200
    status, body = gated.post(gated.RUNNER + "/v1/run", {**req, "capabilities": caps or {}})
    check("capabilities the agent spent itself: refused", spent and refused(status, body) and not body.get("missing"), body)


def strict_body():
    raw = b'{"command":{"program":"ls","args":[]},"command":{"program":"touch","args":["canary-dup"]}}'
    status, _ = gated.post(gated.RUNNER + "/v1/run", None, raw=raw)
    check("a body with a key twice: refused", status == 400, status)


def symlinks():
    gated.write("mklink.py", "import os\nos.symlink('/etc', '/work/etc-link')\n")
    status, body = gated.run("python3", ["mklink.py"])
    made = status == 200 and (body.get("result") or {}).get("exit_code") == 0
    status, body = gated.run("cat", ["etc-link/hostname"])
    check("reading through a symbolic link: refused", made and refused(status, body) and "symbolic link" in body.get("reason", ""), body)
    status, body = gated.write("etc-link/canary-link", "x")
    check("writing through a symbolic link: refused", refused(status, body), body)


def leftovers():
    # A child that leaves the process group and the session, then writes a
    # canary two seconds later. The executor stops it when the command ends.
    gated.write("leftover.py", "\n".join([
        "import os, time",
        "if os.fork() == 0:",
        "    os.setsid()",
        "    if os.fork() == 0:",
        "        time.sleep(2)",
        "        open('/work/canary-leftover', 'w').write('survived')",
        "    os._exit(0)",
        "print('parent done')",
        "",
    ]))
    status, body = gated.run("python3", ["leftover.py"])
    check("a command that leaves a process behind runs", status == 200, body)
    time.sleep(4)


def sandbox_network():
    gated.write("net.py", "\n".join([
        "import socket",
        "for host, port in [('gate', 8421), ('runner', 8431), ('1.1.1.1', 443)]:",
        "    try:",
        "        socket.create_connection((host, port), timeout=2).close()",
        "        print('reached', host)",
        "    except OSError:",
        "        print('no route to', host)",
        "",
    ]))
    status, body = gated.run("python3", ["net.py"])
    out = (body.get("result") or {}).get("stdout", "")
    check("code in the sandbox reaches no network", status == 200 and "reached" not in out and out.count("no route") == 3, body)


def socket_takeover():
    # Code the policy lets run shares the executor's user: it can delete the
    # executor's socket and bind its own in its place. The next command must
    # still reach the real executor.
    gated.write("take.py", "\n".join([
        "import os, socket",
        "p = '/run/archytan-runner/exec.sock'",
        "os.unlink(p)",
        "s = socket.socket(socket.AF_UNIX)",
        "s.bind(p)",
        "s.listen(1)",
        "os.chmod(p, 0)",
        "print('taken')",
        "",
    ]))
    status, body = gated.run("python3", ["take.py"])
    took = status == 200 and (body.get("result") or {}).get("stdout") == "taken\n"
    status, body = gated.run("echo", ["still here"])
    check("code that takes the executor's socket doesn't cut the runner off",
          took and status == 200 and (body.get("result") or {}).get("stdout") == "still here\n", (took, status, body))


def executor_direct():
    # Code the policy lets run can reach the executor's socket from inside
    # the sandbox. Asked directly, the executor must refuse anything but a
    # ping, or the code could queue work that runs after it, unpaid for.
    gated.write("direct.py", "\n".join([
        "import json, socket",
        "for req in [{'op': 'write', 'path': '/work/canary-direct', 'content': 'x'},",
        "            {'op': 'run', 'program': 'touch', 'args': ['canary-direct-run'], 'cwd': '/work'},",
        "            {'op': 'ping'}]:",
        "    s = socket.socket(socket.AF_UNIX)",
        "    s.connect('/run/archytan-runner/exec.sock')",
        "    s.sendall(json.dumps(req).encode())",
        "    s.shutdown(socket.SHUT_WR)",
        "    print(req['op'], s.makefile().read().strip())",
        "",
    ]))
    status, body = gated.run("python3", ["direct.py"])
    out = (body.get("result") or {}).get("stdout", "")
    refused = out.count("only from the runner") == 2
    pinged = '\nping {"exit_code":0' in "\n" + out
    check("code in the sandbox can't use the executor directly (a ping still works)", status == 200 and refused and pinged, out or body)


def gate_down_1():
    req = {"command": {"program": "touch", "args": ["canary-gate-down"]}}
    _, p = gated.plan(req)
    caps, _ = gated.capabilities_for(p)
    with open("/tmp/gate-down.json", "w") as f:
        json.dump({**req, "capabilities": caps or {}}, f)
    check("capabilities ready; stop the gate now", issued(caps), caps)


def gate_down_2():
    with open("/tmp/gate-down.json") as f:
        body = json.load(f)
    status, out = gated.post(gated.RUNNER + "/v1/run", body)
    # 503: the runner couldn't reach the gate. A 403 would mean it refused
    # for another reason, such as a missing capability.
    check("gate down: refused, nothing runs", status == 503 and refused(status, out), (status, out))
    # The client asking for a capability says the gate didn't answer,
    # rather than that it refused.
    status, out = gated.run("ls", [])
    check("gate down: the client reports no answer, not a refusal",
          status == 0 and "didn't answer" in out.get("error", ""), (status, out))


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else "all"
    if step == "gate-down-1":
        gate_down_1()
    elif step == "gate-down-2":
        gate_down_2()
    else:
        for attack in (environment, through_the_gate, without_capabilities, replay, wrong_path,
                       wrong_action, glued_path, policy_refusals, forged, spent_by_agent, strict_body,
                       symlinks, leftovers, sandbox_network, socket_takeover, executor_direct):
            attack()
    sys.exit(0 if all(results) else 1)
