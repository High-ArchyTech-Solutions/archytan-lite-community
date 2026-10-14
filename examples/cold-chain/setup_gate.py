r"""One-time setup for the cold-chain demo: the gate's signing key and the
three credentials. Safe to run again; it keeps whatever already exists.

  gate\public_key.hex         the gate's public key, to verify receipts
  gate\callers.json           SHA-256 digests of the credentials, with roles
  secrets\remote-mgmt.token   the remote management platform's gate credential
                              (role remote_operator). The attacker has this.
  secrets\controller-api.key  the controllers' own API key, which the
                              management platform holds. The attacker has this.
  secrets\engineer.token      an on-site refrigeration engineer's credential
                              (role refrigeration_engineer). Held elsewhere.
  secrets\controller-guard.token  what the controllers use to spend
                              capabilities. It is allowed no action at all.

The tools come from the published image (keygen and callergen ship in it),
and the signing key is made inside a Docker volume it never leaves.
"""
import argparse
import json
import os
import re
import secrets
import subprocess

import gates

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, "gate")
SECRETS = os.path.join(HERE, "secrets")

CALLERS = [
    ("remote-mgmt", "remote_operator", "remote-mgmt.token"),
    ("engineer-onsite", "refrigeration_engineer", "engineer.token"),
    ("controller-guard", "controller", "controller-guard.token"),
]


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def make_key(image):
    public_key_path = os.path.join(GATE, "public_key.hex")
    if os.path.exists(public_key_path) and gates.key_in_volume(GATE, image):
        return
    # A fresh volume, mounted where the image keeps /data so it starts out
    # owned by the gate's own user, which then reads the key.
    volume = gates.keys_volume(GATE)
    gates.docker("volume", "rm", "-f", volume, check=False)
    gates.docker("volume", "create", volume)
    out = run(["docker", "run", "--rm", "--mount", f"type=volume,source={volume},target=/data",
               "--entrypoint", "keygen", image, "-out", "/data/signing_key.pem"])
    public_key = re.search(r"public key[^:]*:\s*([0-9a-f]{64})", out).group(1)
    with open(public_key_path, "w") as f:
        f.write(public_key + "\n")
    print(f"gate signing key created in Docker volume {volume}")


def make_callers(image):
    callers_path = os.path.join(GATE, "callers.json")
    if os.path.exists(callers_path):
        return
    records = []
    for caller_id, role, token_file in CALLERS:
        out = run(["docker", "run", "--rm", "--entrypoint", "callergen", image,
                   "-caller-id", caller_id, "-role", role])
        token = re.search(r"store it now:\s+(\S+)", out).group(1)
        records.append(json.loads(re.search(r"(\{.*?\})", out, re.S).group(1)))
        with open(os.path.join(SECRETS, token_file), "w") as f:
            f.write(token + "\n")
    with open(callers_path, "w") as f:
        json.dump({"version": 1, "callers": records}, f, indent=2)
        f.write("\n")
    print("credentials created:", ", ".join(c[0] for c in CALLERS))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", default=gates.DEFAULT_IMAGE)
    args = parser.parse_args()
    os.makedirs(GATE, exist_ok=True)
    os.makedirs(SECRETS, exist_ok=True)

    make_key(args.image)
    make_callers(args.image)

    api_key_path = os.path.join(SECRETS, "controller-api.key")
    if not os.path.exists(api_key_path):
        with open(api_key_path, "w") as f:
            f.write(secrets.token_urlsafe(24) + "\n")
        print("controller API key created")


if __name__ == "__main__":
    main()
