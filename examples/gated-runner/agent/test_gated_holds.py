"""Tests for how gated.py waits for a human hold. No Docker, no network.

A fake gate and a fake runner listen on 127.0.0.1 in threads. Run it with:

    python -I test_gated_holds.py

Each test shows what the agent does when the gate holds one path of a
command for a person: waits, collects with the same request, re-asks the
other paths, and runs; or refuses, and the runner is never asked to run.
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # python -I leaves the script folder off the path
import gated  # noqa: E402

PATHS = ["/work", "/work/a"]
HELD = "/work/a"
FAR = "2099-01-01T00:00:00.000Z"


class Fake:
    """What the fake gate and runner have seen, and how the gate answers."""

    def __init__(self):
        self.lock = threading.Lock()
        self.authorize = []  # (resource id, idempotency key, body)
        self.polls = 0
        self.runs = []
        self.hold_state = "open"
        self.hold_expires = FAR
        self.hold_with_id = True
        self.held_paths = {HELD}
        self.n = 0
        self.flip_at_poll = None  # (poll number, state): a person decides after that many reads
        self.keys = {}  # idempotency key -> hold id (held paths only)

    def handle_authorize(self, body):
        rid = body["resource"]["id"]
        key = body["idempotency_key"]
        with self.lock:
            self.authorize.append((rid, key, body))
            self.n += 1
            n = self.n
            if rid not in self.held_paths:
                return 200, {"decision": "ALLOW", "capability": {"token": f"cap{n}"}}
            hid = self.keys.setdefault(key, f"rcpt_{len(self.keys) + 1}")
            if self.hold_state == "open":
                if not self.hold_with_id:
                    return 202, {"decision": "HOLD", "receipt": {"receipt_id": hid, "decision": "HOLD"}, "hold": {}}
                return 202, {"decision": "HOLD", "receipt": {"receipt_id": hid, "decision": "HOLD"},
                             "hold": {"hold_id": hid, "expires_at": self.hold_expires}}
            if self.hold_state == "approved":
                return 200, {"decision": "ALLOW", "capability": {"token": f"held-cap{n}"},
                             "receipt": {"receipt_id": "rcpt_res", "decision": "ALLOW"},
                             "hold_receipt": {"receipt_id": hid, "decision": "HOLD"}}
            return 403, {"decision": "BLOCK"}

    def handle_hold(self, hid):
        with self.lock:
            self.polls += 1
            if self.flip_at_poll and self.polls >= self.flip_at_poll[0]:
                self.hold_state = self.flip_at_poll[1]
            if hid not in self.keys.values():
                return 404, {"error": "not found"}
            return 200, {"hold_id": hid, "state": self.hold_state, "expires_at": self.hold_expires}


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class HoldWaitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = Fake()
        fake = cls.fake

        class Gate(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.reply(*fake.handle_authorize(body))

            def do_GET(self):
                self.reply(*fake.handle_hold(self.path.rsplit("/", 1)[1]))

        class Runner(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/v1/plan":
                    out = {"action": "file.read", "resources": PATHS}
                else:
                    fake.runs.append(body)
                    out = {"decision": "ALLOW", "result": {"stdout": "ok", "exit_code": 0}}
                data = json.dumps(out).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        cls.gate_srv = serve(Gate)
        cls.runner_srv = serve(Runner)
        gated.GATE = f"http://127.0.0.1:{cls.gate_srv.server_port}"
        gated.RUNNER = f"http://127.0.0.1:{cls.runner_srv.server_port}"
        cls.tokfile = tempfile.NamedTemporaryFile("w", delete=False)
        cls.tokfile.write("agent-token\n")
        cls.tokfile.close()
        os.environ["AGENT_TOKEN_PATH"] = cls.tokfile.name

    @classmethod
    def tearDownClass(cls):
        cls.gate_srv.shutdown()
        cls.runner_srv.shutdown()
        os.unlink(cls.tokfile.name)

    def setUp(self):
        self.fake.__dict__.clear()
        self.fake.__init__()
        os.environ["HOLD_WAIT_SECONDS"] = "5"
        os.environ["HOLD_POLL_SECONDS"] = "0.05"

    def test_all_allowed_is_unchanged(self):
        self.fake.held_paths = set()
        status, body = gated.run("cat", ["a"])
        self.assertEqual(status, 200)
        self.assertEqual(body["decision"], "ALLOW")
        self.assertEqual(len(self.fake.authorize), 2)
        self.assertEqual(self.fake.polls, 0)
        self.assertEqual(len(self.fake.runs), 1)

    def test_held_then_approved_collects_and_runs(self):
        self.fake.flip_at_poll = (3, "approved")
        status, body = gated.run("cat", ["a"])
        self.assertEqual(status, 200, body)
        self.assertEqual(len(self.fake.runs), 1)
        caps = self.fake.runs[0]["capabilities"]
        self.assertEqual(sorted(caps), sorted(PATHS))
        self.assertTrue(caps[HELD].startswith("held-cap"), caps)
        self.assertGreater(self.fake.polls, 0)
        asks = [(r, k, b) for r, k, b in self.fake.authorize]
        held = [a for a in asks if a[0] == HELD]
        self.assertEqual(len(held), 2)
        self.assertEqual(held[0][1], held[1][1], "collected with the same key")
        self.assertEqual(held[0][2], held[1][2], "collected with the same body")
        others = [a for a in asks if a[0] != HELD]
        self.assertEqual(len(others), 2, "the other path is asked again after the wait")
        self.assertNotEqual(others[0][1], others[1][1], "with a new key")
        self.assertTrue(caps["/work"].startswith("cap"))
        self.assertNotEqual(caps["/work"], "cap1", "the cap from before the wait isn't reused")

    def test_held_then_denied_runs_nothing(self):
        self.fake.flip_at_poll = (2, "denied")
        status, body = gated.run("cat", ["a"])
        self.assertEqual(status, 403)
        self.assertEqual(body["decision"], "BLOCK")
        self.assertIn(HELD, body["reason"])
        self.assertIn("denied", body["reason"])
        self.assertEqual(self.fake.runs, [])
        self.assertEqual(len([a for a in self.fake.authorize if a[0] == HELD]), 1, "nothing was collected")

    def test_held_and_never_answered_hits_the_deadline(self):
        os.environ["HOLD_WAIT_SECONDS"] = "0.5"
        start = time.time()
        status, body = gated.run("cat", ["a"])
        self.assertLess(time.time() - start, 4)
        self.assertEqual(status, 403)
        self.assertIn("nobody answered", body["reason"])
        self.assertIn(HELD, body["reason"])
        self.assertEqual(self.fake.runs, [])
        self.assertGreater(self.fake.polls, 1)

    def test_hold_expiry_ends_the_wait_early(self):
        os.environ["HOLD_WAIT_SECONDS"] = "60"
        self.fake.hold_expires = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 10))
        start = time.time()
        status, body = gated.run("cat", ["a"])
        self.assertLess(time.time() - start, 5)
        self.assertEqual(status, 403)
        self.assertEqual(self.fake.runs, [])

    def test_wait_zero_refuses_at_once(self):
        os.environ["HOLD_WAIT_SECONDS"] = "0"
        status, body = gated.run("cat", ["a"])
        self.assertEqual(status, 403)
        self.assertIn("HOLD_WAIT_SECONDS is 0", body["reason"])
        self.assertEqual(self.fake.polls, 0)
        self.assertEqual(self.fake.runs, [])

    def test_202_without_a_hold_id_is_refused_not_held(self):
        self.fake.hold_with_id = False
        status, body = gated.run("cat", ["a"])
        self.assertEqual(status, 403)
        self.assertIn("without a valid hold", body["reason"])
        self.assertEqual(self.fake.polls, 0)
        self.assertEqual(self.fake.runs, [])

    def test_a_hold_approved_but_not_collectable_runs_nothing(self):
        # The gate says approved when read, then answers 403 when collected.
        orig = self.fake.handle_authorize

        def collect_fails(body):
            if body["resource"]["id"] == HELD and self.fake.hold_state == "approved":
                return 410, {"error": "gone"}
            return orig(body)
        self.fake.handle_authorize = collect_fails
        self.fake.flip_at_poll = (2, "approved")
        status, body = gated.run("cat", ["a"])
        self.assertEqual(status, 403)
        self.assertIn("couldn't be collected", body["reason"])
        self.assertEqual(self.fake.runs, [])


if __name__ == "__main__":
    unittest.main()
