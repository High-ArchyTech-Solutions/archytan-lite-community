r"""Runs both scenarios and checks every outcome against expected.json.

  python validate.py                  check both scenarios
  python validate.py --skip-verifier  without the standalone Go verifier

For each scenario (standard, and --revoke) it runs demo.py, compares the
run's results.json with expected.json, and then checks the auditor's export
with the standalone verifier the export ships, which needs Go. Reports every
check, then exits non-zero if any failed.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


class Checks:
    def __init__(self):
        self.failed = 0

    def check(self, label, ok, detail=""):
        print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail and not ok else ""))
        if not ok:
            self.failed += 1


def compare(checks, path, expected, actual):
    """Every key in expected must match; extra keys in actual are ignored."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            checks.check(path, False, f"expected an object, got {actual!r}")
            return
        for key, value in expected.items():
            compare(checks, f"{path}.{key}" if path else key, value, actual.get(key))
    else:
        checks.check(f"{path} = {expected!r}", expected == actual, f"got {actual!r}")


def run_scenario(checks, scenario, expected, args):
    print(f"\n{scenario}")
    cmd = [sys.executable, os.path.join(HERE, "demo.py"), "--image", args.image]
    if args.gate:
        cmd += ["--gate", args.gate]
    if scenario == "revoke":
        cmd.append("--revoke")
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = re.sub(r"\x1b\[[0-9;]*m", "", proc.stdout)
    match = re.search(r"Transcript: (.+?)demo-output\.txt", out)
    checks.check("demo.py ran to completion", proc.returncode == 0 and match is not None,
                  (proc.stderr or out)[-600:])
    if not match:
        return
    run_dir = os.path.join(HERE, match.group(1).strip())
    with open(os.path.join(run_dir, "results.json"), encoding="utf-8") as f:
        results = json.load(f)
    print(f"  run: {os.path.relpath(run_dir, HERE)}  ({results['gate']})")

    evidence_expected = dict(expected["evidence"])
    prefix = evidence_expected.pop("verify_chain_prefix")
    per_policy = evidence_expected.pop("decisions_per_policy")
    compare(checks, "", {k: v for k, v in expected.items() if k != "evidence"}, results)
    compare(checks, "evidence", evidence_expected, results["evidence"])
    checks.check(f"--verify-chain reports '{prefix}'", results["evidence"]["verify_chain"].startswith(prefix),
                 results["evidence"]["verify_chain"])
    counts = list(results["evidence"]["decisions_per_policy"].values())
    checks.check(f"decisions per policy, in order = {per_policy}", counts == per_policy, f"got {counts}")

    if args.skip_verifier:
        return
    if not shutil.which("go"):
        checks.check("standalone verifier (needs Go; pass --skip-verifier to skip)", False, "go not on the PATH")
        return
    with open(os.path.join(HERE, "gate", "public_key.hex")) as f:
        key = f.read().strip()
    verifier = subprocess.run(["go", "run", ".", "--export", "..", "--trusted-keys", key],
                              cwd=os.path.join(run_dir, "evidence", "verifier"),
                              capture_output=True, text=True)
    out = verifier.stdout + verifier.stderr
    total = expected["evidence"]["receipts"]["total"]
    checks.check("standalone verifier exits 0", verifier.returncode == 0, out[-600:])
    for line in ("manifest OK", "receipts OK", f"chain OK: {total} receipt(s) verified, unbroken from genesis"):
        checks.check(f"standalone verifier: {line}", line in out, out[-600:])


def main():
    sys.path.insert(0, HERE)
    import gates

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gate", help="passed to demo.py (default: demo.py's)")
    parser.add_argument("--image", default=gates.DEFAULT_IMAGE)
    parser.add_argument("--skip-verifier", action="store_true")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    with open(os.path.join(HERE, "expected.json"), encoding="utf-8") as f:
        expected = json.load(f)
    checks = Checks()
    for scenario in ("standard", "revoke"):
        run_scenario(checks, scenario, expected[scenario], args)
    print(f"\n{'all checks passed' if not checks.failed else f'{checks.failed} check(s) FAILED'}")
    sys.exit(1 if checks.failed else 0)


if __name__ == "__main__":
    main()
