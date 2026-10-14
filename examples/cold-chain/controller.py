"""A simulated refrigeration controller estate for the cold-chain demo.

It isn't a model of any vendor's controller. It holds the 36 cabinets in
coldchain.py, takes commands over HTTP, and simulates temperatures on a clock
the demo advances (POST /sim/advance), so a run is repeatable.

  --mode open      How remote management usually works: a command carrying
                   the controllers' API key is executed. Whoever holds that
                   key, the management platform or someone who has taken it
                   over, holds the controllers' full authority.

  --mode guarded   The API key is still required, but it no longer carries
                   authority on its own. Each command must come with a
                   capability from the Archytan Lite gate. The controller
                   works out which gated action the command amounts to from
                   the command itself, then spends the capability for exactly
                   that action on exactly that cabinet before it acts. With no
                   capability, or one minted for anything else, nothing moves.
"""
import argparse
import hmac
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import archytan_lite

import coldchain

HERE = os.path.dirname(os.path.abspath(__file__))

AMBIENT = 20.0          # a cabinet with no cooling drifts toward store air
WARM_RATE = 0.12        # degrees per simulated minute without cooling
COOL_RATE = 0.30        # degrees per simulated minute with the compressor on
DEFROST_RATE = 0.15     # degrees per simulated minute while the heaters run
DEFROST_MINUTES = 30    # one defrost cycle


def read_secret(name):
    with open(os.path.join(HERE, "secrets", name)) as f:
        return f.read().strip()


class Cabinet:
    def __init__(self, cabinet_id):
        self.id = cabinet_id
        self.kind = coldchain.kind(cabinet_id)
        self.temp = coldchain.START[self.kind]
        self.setpoint = self.temp
        self.peak = self.temp
        self.defrost_left = 0
        self.compressor_on = True
        self.alarm_high = coldchain.BREACH[self.kind]

    def step(self):
        """One simulated minute."""
        if self.defrost_left > 0:
            self.temp += DEFROST_RATE
            self.defrost_left -= 1
        elif not self.compressor_on:
            self.temp = min(AMBIENT, self.temp + WARM_RATE)
        else:
            target = min(self.setpoint, AMBIENT)
            if self.temp < target:
                self.temp = min(target, self.temp + WARM_RATE)
            elif self.temp > target:
                self.temp = max(target, self.temp - COOL_RATE)
        self.peak = max(self.peak, self.temp)

    def execute(self, command, value):
        if command == "set_temperature":
            self.setpoint = value
        elif command == "start_defrost":
            self.defrost_left = DEFROST_MINUTES
        elif command == "stop_compressor":
            self.compressor_on = False
        elif command == "change_alarm_threshold":
            self.alarm_high = value

    def status(self):
        return {
            "cabinet": self.id, "kind": self.kind,
            "temp": round(self.temp, 1), "setpoint": self.setpoint, "peak": round(self.peak, 1),
            "defrosting": self.defrost_left > 0, "compressor_on": self.compressor_on,
            "breached": self.peak > coldchain.BREACH[self.kind],
        }


class Estate:
    def __init__(self, mode, gate_url):
        self.mode = mode
        self.gate_url = gate_url
        self.api_key = read_secret("controller-api.key")
        self.guard_token = read_secret("controller-guard.token") if mode == "guarded" else None
        self.cabinets = {cid: Cabinet(cid) for cid in coldchain.cabinet_ids()}
        self.lock = threading.Lock()

    def command(self, api_key, body):
        """Returns (http status, response body)."""
        if not hmac.compare_digest(api_key.encode(), self.api_key.encode()):
            return 401, {"executed": False, "reason": "bad API key"}

        cabinet_id = body.get("cabinet")
        command = body.get("command")
        value = body.get("value")
        if cabinet_id not in self.cabinets:
            return 404, {"executed": False, "reason": "no such cabinet"}
        if command not in coldchain.COMMANDS:
            return 400, {"executed": False, "reason": "unknown command"}
        if command in ("set_temperature", "change_alarm_threshold") and not isinstance(value, (int, float)):
            return 400, {"executed": False, "reason": "a numeric value is required"}

        if self.mode == "guarded":
            capability = body.get("capability")
            if not capability:
                log(f"REFUSED  {cabinet_id} {command} {fmt(value)}: no capability")
                return 403, {"executed": False, "reason": "no capability: this controller acts only on a capability from the gate"}
            # The action comes from the command about to run, never from the
            # sender, so a capability for a harmless change can't carry a
            # harmful one.
            action = coldchain.action_for(cabinet_id, command, value)
            try:
                redeemed = archytan_lite.redeem_capability(
                    url=self.gate_url, token=self.guard_token, capability_token=capability,
                    action=action, resource={"type": "cabinet", "id": cabinet_id},
                )
            except Exception as err:
                log(f"REFUSED  {cabinet_id} {command} {fmt(value)}: gate communication error: {err}")
                return 503, {"executed": False, "reason": f"gate communication failure: {err}"}
            if not redeemed.redeemed:
                log(f"REFUSED  {cabinet_id} {command} {fmt(value)}: gate refused to redeem for {action}")
                return 403, {"executed": False, "action": action, "reason": redeemed.reason}

        with self.lock:
            self.cabinets[cabinet_id].execute(command, value)
        log(f"EXECUTED {cabinet_id} {command} {fmt(value)}")
        return 200, {"executed": True}

    def advance(self, minutes):
        with self.lock:
            for _ in range(minutes):
                for cabinet in self.cabinets.values():
                    cabinet.step()

    def status(self):
        with self.lock:
            return [c.status() for c in self.cabinets.values()]


def fmt(value):
    return "" if value is None else f"{value:+g}"


def log(line):
    print(line, flush=True)


def make_handler(estate):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def read_json(self):
            length = int(self.headers.get("Content-Length") or 0)
            try:
                return json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return None

        def do_GET(self):
            if self.path == "/status":
                self.reply(200, {"mode": estate.mode, "cabinets": estate.status()})
            else:
                self.reply(404, {})

        def do_POST(self):
            body = self.read_json()
            if not isinstance(body, dict):
                self.reply(400, {"executed": False, "reason": "body must be a JSON object"})
            elif self.path == "/command":
                auth = self.headers.get("Authorization", "")
                api_key = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
                self.reply(*estate.command(api_key, body))
            elif self.path == "/sim/advance":
                estate.advance(int(body.get("minutes", 0)))
                self.reply(200, {"advanced": body.get("minutes", 0)})
            else:
                self.reply(404, {})

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=("open", "guarded"), required=True)
    parser.add_argument("--addr", default="127.0.0.1:8441")
    parser.add_argument("--gate", default="http://127.0.0.1:8431")
    args = parser.parse_args()

    host, port = args.addr.rsplit(":", 1)
    estate = Estate(args.mode, args.gate)
    server = ThreadingHTTPServer((host, int(port)), make_handler(estate))
    log(f"controller estate up in {args.mode} mode on {args.addr}, {len(estate.cabinets)} cabinets")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
