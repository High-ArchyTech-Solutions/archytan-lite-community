#!/usr/bin/env sh
# Runs the cold-chain experiment on macOS and Linux. The first run creates
# the Python environment, the gate's signing key and the three credentials;
# later runs reuse them. Needs Docker and Python 3.10 or later.
#
#   ./run-demo.sh            run straight through
#   ./run-demo.sh --pause    wait for Enter between parts
#   ./run-demo.sh --revoke   add part 2e: the operator revokes the platform's role
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
    .venv/bin/python -m pip install -q -r requirements.txt
fi
.venv/bin/python setup_gate.py
.venv/bin/python demo.py "$@"
