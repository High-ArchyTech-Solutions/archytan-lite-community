r"""Cold-chain demo: a compromised remote management platform against a
refrigeration estate, first with nothing between it and the controllers, then
with Archytan Lite in the command path.

Everything runs on this machine: the real Archytan Lite gate (the published
image, as a container), a simulated controller estate (controller.py) and the
official Python client. Each run gets its own decision log, evidence and
results.json under runs/.

  python demo.py             run straight through
  python demo.py --pause     wait for Enter between parts (for recording)
  python demo.py --revoke    add part 2e: revoke the platform's role mid-incident
"""
import argparse
import datetime
import glob
import hashlib
import importlib.metadata
import inspect
import json
import os
import platform
import re
import subprocess
import sys
import time
import uuid

import archytan_lite
import httpx

import coldchain
import gates

HERE = os.path.dirname(os.path.abspath(__file__))
# A gates_<name>.py beside this file can add more ways to run the gate to
# gates.GATES.
for _module in sorted(glob.glob(os.path.join(HERE, "gates_*.py"))):
    importlib.import_module(os.path.splitext(os.path.basename(_module))[0])
GATE_DIR = os.path.join(HERE, "gate")
GATE_ADDR = "127.0.0.1:8431"
GATE_URL = f"http://{GATE_ADDR}"
CONTROLLER_ADDR = "127.0.0.1:8441"
CONTROLLER_URL = f"http://{CONTROLLER_ADDR}"
SHOWCASE = "store-0417/frozen-01"
INSTANCE_URN = "urn:archytan-lite:instance:coldchain-demo"

GREEN, RED, AMBER, DIM, BOLD, RESET = "\033[92m", "\033[91m", "\033[93m", "\033[2m", "\033[1m", "\033[0m"
ANSI = re.compile(r"\033\[[0-9;]*m")


def read(path):
    with open(path) as f:
        return f.read().strip()


PUBLIC_KEY = read(os.path.join(GATE_DIR, "public_key.hex"))
API_KEY = read(os.path.join(HERE, "secrets", "controller-api.key"))

# The demo's own calls to the simulated controllers. Calls to the gate go
# through the official client.
CONTROLLERS = httpx.Client(timeout=5)


def sha256_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def write_provenance(run_dir, gate):
    """Records what this run actually ran: the gate (the published image and
    its digest), the controller-side code and configuration the result
    depends on, and the client. Returns a one-line summary for the
    transcript."""
    gate_record, summary, _ = gate.provenance()
    files = ["coldchain.py", "controller.py", "demo.py", "gates.py", os.path.basename(inspect.getfile(type(gate))),
             os.path.join("gate", "policy.json"), os.path.join("gate", "callers.json")]
    record = {
        "run_started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "gate_instance_urn": INSTANCE_URN,
        "gate_public_key": PUBLIC_KEY,
        "gate": gate_record,
        # The trusted controller side: the classifier and its band table, the
        # guard, and the gate's configuration.
        "trusted_code_and_config_sha256": {
            name: sha256_file(os.path.join(HERE, name))
            for name in files if os.path.exists(os.path.join(HERE, name))
        },
        "client": {
            "archytan-lite": importlib.metadata.version("archytan-lite"),
            "httpx": importlib.metadata.version("httpx"),
            "python": platform.python_version(),
        },
        "os": platform.platform(),
    }
    with open(os.path.join(run_dir, "provenance.json"), "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
        f.write("\n")
    return summary


class Transcript:
    def __init__(self, path):
        self.file = open(path, "w", encoding="utf-8")

    def say(self, text=""):
        print(text, flush=True)
        self.file.write(ANSI.sub("", text) + "\n")
        self.file.flush()


class Party:
    """Someone who sends commands: the management platform or an engineer."""

    def __init__(self, name, token_file):
        self.name = name
        self.gate_token = read(os.path.join(HERE, "secrets", token_file))

    def authorize(self, cabinet, action):
        return archytan_lite.authorize(
            url=GATE_URL, token=self.gate_token, gate_public_key_hex=PUBLIC_KEY,
            action=action, actor={"uid": self.name}, resource={"type": "cabinet", "id": cabinet},
            idempotency_key=str(uuid.uuid4()),
        )

    def send(self, cabinet, command, value=None, capability=None):
        body = {"cabinet": cabinet, "command": command}
        if value is not None:
            body["value"] = value
        if capability is not None:
            body["capability"] = capability
        r = CONTROLLERS.post(f"{CONTROLLER_URL}/command", json=body,
                             headers={"Authorization": f"Bearer {API_KEY}"}, timeout=5)
        return r.json()

    def via_gate(self, cabinet, command, value=None, ask_for=None):
        """Ask the gate, then send. ask_for overrides the action requested,
        which is how a sender lies about what it is going to do."""
        action = ask_for or coldchain.action_for(cabinet, command, value)
        decision = self.authorize(cabinet, action)
        if not decision.allowed:
            return {"decision": "BLOCK", "executed": False}
        result = self.send(cabinet, command, value, decision.capability.token)
        return {"decision": "ALLOW", "executed": result.get("executed", False)}


def wait_for(url, what, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    sys.exit(f"{what} didn't come up at {url}")


def make_gate(args, run_dir):
    cfg = {"gate_dir": GATE_DIR, "addr": GATE_ADDR, "url": GATE_URL,
           "instance_urn": INSTANCE_URN, "public_key": PUBLIC_KEY}
    return gates.GATES[args.gate](cfg, run_dir, args.image)


def start_controller(mode, run_dir):
    log = open(os.path.join(run_dir, f"controller-{mode}.log"), "w")
    proc = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "controller.py"), "--mode", mode,
         "--addr", CONTROLLER_ADDR, "--gate", GATE_URL],
        stdout=log, stderr=subprocess.STDOUT)
    wait_for(f"{CONTROLLER_URL}/status", "the controller estate")
    return proc


def stop(proc):
    proc.terminate()
    proc.wait(timeout=10)


def advance(minutes):
    CONTROLLERS.post(f"{CONTROLLER_URL}/sim/advance", json={"minutes": minutes}, timeout=30)
    return {c["cabinet"]: c for c in CONTROLLERS.get(f"{CONTROLLER_URL}/status", timeout=5).json()["cabinets"]}


def temp(value):
    return f"{value:+.1f}°C"


def report(t, cabinets, showcase):
    broken = [c for c in cabinets.values() if c["breached"]]
    colour = RED if broken else GREEN
    t.say(f"  3 simulated hours later: {colour}{BOLD}{len(broken)} of {len(cabinets)} cabinets broke the cold chain{RESET}")
    for cid in showcase:
        c = cabinets[cid]
        state = f"{RED}cold chain broken{RESET}" if c["breached"] else f"{GREEN}within limits{RESET}"
        t.say(f"    {cid:<22} setpoint {temp(c['setpoint']):>9}  now {temp(c['temp']):>8}  peak {temp(c['peak']):>8}  {state}")
    return len(broken)


def tally(results, key="executed"):
    return sum(1 for r in results if r[key])


def decisions(results, decision):
    return sum(1 for r in results if r["decision"] == decision)


def part_one(t, attacker, cabinets):
    t.say(f"{BOLD}1. The controllers trust the management platform{RESET}")
    t.say(f"{DIM}  The attacker holds the platform's credentials and the controllers' API key.{RESET}")
    raised = [attacker.send(c, "set_temperature", 50) for c in cabinets]
    defrost = [attacker.send(c, "start_defrost") for c in cabinets]
    t.say(f"  attacker  set_temperature +50°C on every cabinet    {RED}{tally(raised)} of {len(cabinets)} executed{RESET}")
    t.say(f"  attacker  start_defrost on every cabinet            {RED}{tally(defrost)} of {len(cabinets)} executed{RESET}")
    broken = report(t, advance(180), [SHOWCASE, "store-0417/chilled-01", "store-1120/frozen-02"])
    return {"cabinets": len(cabinets), "setpoint_executed": tally(raised),
            "defrost_executed": tally(defrost), "broke_cold_chain": broken}


def part_revoke(t, attacker, engineer, gate):
    """Part 2e: the operator revokes the platform's role mid-incident, and the
    capabilities the attacker already holds stop working."""
    t.say(f"  {BOLD}e. Incident response: the operator revokes the platform's role{RESET}")
    held_on = ["store-0512/frozen-01", "store-0512/frozen-02", "store-0621/frozen-01"]
    held = [(c, attacker.authorize(c, "refrigeration.setpoint.in_band")) for c in held_on]
    held = [(c, d.capability.token) for c, d in held if d.allowed]
    t.say(f"     the attacker first collects {len(held)} in-band capabilities and holds them, unspent")

    policy = gate.current_policy()
    for rule in policy["actions"]:
        rule["allowed_roles"] = [role for role in rule["allowed_roles"] if role != "remote_operator"]
    how, note = gate.apply_policy(policy)
    t.say(f"     the operator removes remote_operator from the policy and {how}")
    t.say(f"     {DIM}({note}){RESET}")

    spent = [attacker.send(c, "set_temperature", -15, token) for c, token in held]
    t.say(f"     the held capabilities, used for in-band changes     {RED}{len(spent) - tally(spent)} refused{RESET} at redemption, "
          f"{tally(spent)} executed")
    fresh = [attacker.via_gate(c, "set_temperature", -20) for c in held_on]
    t.say(f"     new requests from the platform                      {RED}{decisions(fresh, 'BLOCK')} BLOCK{RESET}, {tally(fresh)} executed")
    r = engineer.via_gate("store-0512/frozen-01", "set_temperature", -21)
    t.say(f"     the engineer's routine change                       {GREEN}{r['decision']}{RESET}, executed: {r['executed']}")
    return {"held": len(held), "held_refused": len(spent) - tally(spent), "held_executed": tally(spent),
            "new_block": decisions(fresh, "BLOCK"), "new_executed": tally(fresh),
            "engineer": r["decision"], "engineer_executed": r["executed"]}


def part_two(t, attacker, engineer, cabinets, incident_response=None):
    t.say(f"{BOLD}2. The same estate with Archytan Lite in the command path{RESET}")
    t.say(f"{DIM}  Each controller now acts only on a capability from the gate, spent for the exact{RESET}")
    t.say(f"{DIM}  command it is about to run. The attacker still holds everything from part 1.{RESET}")
    out = {}

    t.say(f"  {BOLD}Routine work{RESET}")
    r = attacker.via_gate(SHOWCASE, "set_temperature", -22)
    t.say(f"  platform  set_temperature -22°C {SHOWCASE:<22}  {GREEN}{r['decision']}{RESET}, executed: {r['executed']}")
    out["routine_platform"] = r
    r = engineer.via_gate("store-0417/frozen-02", "set_temperature", -26)
    t.say(f"  engineer  set_temperature -26°C store-0417/frozen-02    {GREEN}{r['decision']}{RESET}, executed: {r['executed']}"
          f"  {DIM}(outside the band, so engineer only){RESET}")
    out["routine_engineer"] = r

    t.say(f"  {BOLD}The attack, four ways{RESET}")
    direct = [attacker.send(c, "set_temperature", 50) for c in cabinets]
    t.say(f"  a. +50°C straight to the controllers, no capability    {GREEN}{len(direct) - tally(direct)} refused{RESET}, {tally(direct)} executed")
    out["a_direct"] = {"refused": len(direct) - tally(direct), "executed": tally(direct)}

    asked = [attacker.via_gate(c, "set_temperature", 50) for c in cabinets]
    asked += [attacker.via_gate(c, "stop_compressor") for c in cabinets]
    t.say(f"  b. asks the gate for +50°C and for compressor stop     {RED}{decisions(asked, 'BLOCK')} BLOCK{RESET}, {tally(asked)} executed")
    out["b_asked"] = {"block": decisions(asked, "BLOCK"), "executed": tally(asked)}

    switched = [attacker.via_gate(c, "set_temperature", 50, ask_for="refrigeration.setpoint.in_band") for c in cabinets]
    allowed = decisions(switched, "ALLOW")
    t.say(f"  c. asks for an in-band change, sends +50°C with it     {GREEN}{allowed} ALLOW{RESET} at the gate, "
          f"{RED}{allowed - tally(switched)} refused{RESET} at redemption, {tally(switched)} executed")
    t.say(f"     {DIM}The controller asked to spend each one for an out-of-band change. The in-band grants stay{RESET}")
    t.say(f"     {DIM}unspent: each could only set its one cabinet inside the band, and expires in 30 seconds.{RESET}")
    out["c_switched"] = {"allow": allowed, "refused_at_redemption": allowed - tally(switched), "executed": tally(switched)}

    defrost = [attacker.via_gate(c, "start_defrost") for c in cabinets]
    allowed = decisions(defrost, "ALLOW")
    t.say(f"  d. forced defrost on every cabinet                     {GREEN}{allowed} ALLOW{RESET}, "
          f"{RED}{len(defrost) - allowed} BLOCK{RESET} (3 per hour per credential), {tally(defrost)} executed")
    out["d_defrost"] = {"allow": allowed, "block": len(defrost) - allowed, "executed": tally(defrost)}
    defrosted = [c for c, r in zip(cabinets, defrost) if r["executed"]]

    if incident_response:
        out["e_revoke"] = incident_response()

    status = advance(180)
    out["broke_cold_chain"] = report(t, status, [SHOWCASE, "store-0417/frozen-02", "store-0417/chilled-01"] + defrosted[:1])
    if defrosted:
        t.say(f"  {DIM}The {len(defrosted)} allowed defrosts ran one normal cycle each and recovered without leaving their limits.{RESET}")
    return out


def part_three(t, run_dir, gate, provenance_summary):
    t.say(f"{BOLD}3. The evidence{RESET}")
    out = {}
    # The gate's own tools, run with the settings the gate last ran with, so
    # the export's signed manifest records the instance, the caller model and
    # the policy hash.
    verify = gate.tool("--verify-chain")
    verdict = (verify.stdout + verify.stderr).strip().splitlines()[-1]
    t.say(f"  archytan-lite --verify-chain: {verdict}")
    out["verify_chain"] = verdict

    evidence = os.path.join(run_dir, "evidence")
    gate.export_chain(evidence)
    with open(os.path.join(evidence, "receipts.json"), encoding="utf-8") as f:
        receipts = json.load(f)
    if isinstance(receipts, dict):
        receipts = receipts.get("receipts", [])
    allows = sum(1 for r in receipts if r["decision"] == "ALLOW")
    t.say(f"  {len(receipts)} signed decisions in the chain: {allows} ALLOW, {len(receipts) - allows} BLOCK")
    out["receipts"] = {"total": len(receipts), "allow": allows, "block": len(receipts) - allows}

    refusal = next(r for r in receipts if r["decision"] == "BLOCK" and r["resource_id"] == SHOWCASE
                   and r["action"] == "refrigeration.setpoint.out_of_band")
    problem = archytan_lite.verify_receipt(refusal, PUBLIC_KEY, refusal["idempotency_key"])
    t.say(f"  The gate's refusal of +50°C on {SHOWCASE}, as signed:")
    for field in ("seq", "decision", "action", "resource_type", "resource_id", "caller_id", "actor_role",
                  "policy_sha256", "created_at"):
        if field in refusal:
            value = refusal[field]
            if field == "policy_sha256":
                value = value[:16] + "…"
            t.say(f"    {field:<14} {value}")
    t.say(f"    signature      {'verifies under the gate public key' if problem is None else 'DOES NOT VERIFY: ' + problem}")
    out["showcase_refusal_verifies"] = problem is None

    policies = list(dict.fromkeys(r.get("policy_sha256", "") for r in receipts))
    out["decisions_per_policy"] = {p: sum(1 for r in receipts if r.get("policy_sha256") == p) for p in policies}
    if len(policies) > 1:
        before, after = (sum(1 for r in receipts if r.get("policy_sha256") == p) for p in policies[:2])
        t.say(f"  Each receipt signs the policy it was decided under: {before} under {policies[0][:12]}…, "
              f"{after} under {policies[1][:12]}… after the revocation")

    log = gate.log_text().splitlines()
    rejected = [line for line in log if "capability rejected" in line]
    reason = re.search(r'reason="capability rejected: (?:capability: )?([^"]*)"', rejected[0]).group(1) if rejected else ""
    t.say(f"  Refused redemptions from 2c, in the gate's log with trace ids: {len(rejected)} ({reason})")
    out["logged_redemption_rejections"] = len(rejected)
    revoked = [line for line in log if "would not be made now" in line]
    out["logged_revoked_grant_refusals"] = len(revoked)
    if revoked:
        reason = re.search(r'reason="((?:[^"\\]|\\.)*)"', revoked[0])
        reason = reason.group(1).replace('\\"', '"') if reason else ""
        t.say(f"  Held capabilities refused after the revocation: {len(revoked)} ({reason})")
    t.say(f"  Auditor's copy: {os.path.relpath(evidence, HERE)}  (receipts, signed manifest, standalone verifier)")
    t.say(f"  Provenance: {os.path.relpath(os.path.join(run_dir, 'provenance.json'), HERE)}  ({provenance_summary})")
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pause", action="store_true", help="wait for Enter between parts")
    parser.add_argument("--revoke", action="store_true",
                        help="add part 2e: the operator revokes the platform's role mid-incident")
    parser.add_argument("--gate", choices=sorted(gates.GATES), default="docker",
                        help="how to run the gate (default: docker)")
    parser.add_argument("--image", default=gates.DEFAULT_IMAGE,
                        help=f"the gate image (default {gates.DEFAULT_IMAGE})")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if os.name == "nt":
        os.system("")  # turns on ANSI colours in a Windows console

    run_dir = os.path.join(HERE, "runs", datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(run_dir)
    t = Transcript(os.path.join(run_dir, "demo-output.txt"))
    gate = make_gate(args, run_dir)
    provenance_summary = write_provenance(run_dir, gate)
    cabinets = coldchain.cabinet_ids()
    attacker = Party("remote-mgmt-platform", "remote-mgmt.token")
    engineer = Party("engineer-onsite", "engineer.token")
    results = {"gate": gate.label, "scenario": "revoke" if args.revoke else "standard"}

    def pause():
        t.say()
        if args.pause:
            input(f"{DIM}  Enter to continue{RESET}")

    t.say(f"{BOLD}Cold-chain demo: {len(coldchain.STORES)} stores, {len(cabinets)} refrigerated cabinets, "
          f"one remote management platform{RESET}")
    t.say(f"{DIM}  Simulated controllers; the gate, its policy and its receipts are real ({gate.label}).{RESET}")
    pause()

    try:
        gate.start()
        try:
            controller = start_controller("open", run_dir)
            try:
                results["open"] = part_one(t, attacker, cabinets)
            finally:
                stop(controller)
            pause()

            controller = start_controller("guarded", run_dir)
            try:
                revoke = (lambda: part_revoke(t, attacker, engineer, gate)) if args.revoke else None
                results["guarded"] = part_two(t, attacker, engineer, cabinets, incident_response=revoke)
            finally:
                stop(controller)
            pause()
        finally:
            gate.stop()

        results["evidence"] = part_three(t, run_dir, gate, provenance_summary)
    finally:
        gate.close()

    with open(os.path.join(run_dir, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
        f.write("\n")
    t.say()
    t.say(f"{DIM}  Transcript: {os.path.relpath(os.path.join(run_dir, 'demo-output.txt'), HERE)}{RESET}")


if __name__ == "__main__":
    main()
