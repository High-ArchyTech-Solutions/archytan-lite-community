#!/usr/bin/env bash
# Start a separate copy of the example and attack it from the agent's
# container. Fails if any attempt gets through, if a canary-* file shows up in
# the workspace, or if hello.txt, which the delete attempt goes after, is gone.
#
# It runs as its own Compose project (archytan-gated-runner-test) with its own
# state folder (.test-state), and removes both when it ends: a deployment you
# started with "docker compose up" is never touched.
#
#   ./test-attacks.sh                          with the published image
#   LITE_IMAGE=archytan-lite:dev ./test-attacks.sh
#   KEEP=1 ./test-attacks.sh                   leave the test copy running
set -euo pipefail
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")"
export LITE_IMAGE="${LITE_IMAGE:-ghcr.io/high-archytech-solutions/archytan-lite:2.9.0}"
export COMPOSE_PROJECT_NAME=archytan-gated-runner-test
export STATE_DIR=./.test-state

listener="${COMPOSE_PROJECT_NAME}-host-listener"
hostsvc="${COMPOSE_PROJECT_NAME}-host-service"
cleanup() {
  docker rm -f "$listener" "$hostsvc" >/dev/null 2>&1 || true
  if [ "${KEEP:-}" != 1 ]; then
    docker compose down -v --remove-orphans >/dev/null 2>&1 || true
    rm -rf "$STATE_DIR"
  fi
}
trap cleanup EXIT

./setup.sh
docker compose up -d --build --wait
fail=0

echo "== attacks from the agent"
# The host's address on the agents network; a service listening on the
# host's own network, which the agent can reach (reported, not a failure);
# and a listener Docker publishes on all of the host's addresses, which the
# agent must not reach. Both only accept.
gateway="$(docker network inspect "${COMPOSE_PROJECT_NAME}_agents" --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}')"
docker run -d --rm --name "$hostsvc" --network host --entrypoint python \
  "${COMPOSE_PROJECT_NAME}-agent:local" -c 'import socket
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("", 18997)); s.listen()
while True: s.accept()[0].close()' >/dev/null
docker run -d --rm --name "$listener" -p 18998:8000 --entrypoint python \
  "${COMPOSE_PROJECT_NAME}-agent:local" -c 'import socket
s = socket.socket(); s.bind(("", 8000)); s.listen()
while True: s.accept()[0].close()' >/dev/null
# Control: a container on Docker's default network must reach that port, or
# the agent failing to reach it would prove nothing.
bridge_gw="$(docker network inspect bridge --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}')"
reached=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if docker run --rm --network bridge --entrypoint python "${COMPOSE_PROJECT_NAME}-agent:local" \
      -c 'import socket, sys; socket.create_connection((sys.argv[1], 18998), timeout=2).close()' \
      "$bridge_gw" >/dev/null 2>&1; then
    reached=1
    break
  fi
  sleep 1
done
if [ "$reached" = 1 ]; then
  echo "PASS control: the published port is reachable from Docker's default network"
else
  echo "FAIL control: the published port isn't reachable even from Docker's default network"
  fail=1
fi
docker compose exec -T -e HOST_GATEWAY="$gateway" -e HOST_PUBLISHED_PORT=18998 -e HOST_SERVICE_PORT=18997 \
  agent python attacks.py \
  || { echo "FAIL attacks.py exited non-zero (its own FAIL lines, if any, are above)"; fail=1; }
docker rm -f "$listener" "$hostsvc" >/dev/null 2>&1 || true

echo "== the gate goes down between asking and acting"
docker compose exec -T agent python attacks.py gate-down-1 \
  || { echo "FAIL attacks.py exited non-zero (its own FAIL lines, if any, are above)"; fail=1; }
docker compose stop gate >/dev/null
docker compose exec -T agent python attacks.py gate-down-2 \
  || { echo "FAIL attacks.py exited non-zero (its own FAIL lines, if any, are above)"; fail=1; }
docker compose start gate >/dev/null

echo "== what the workspace holds, seen from the sandbox"
listing="$(docker compose exec -T sandbox ls -A /work)"
printf '%s\n' "$listing" | sed 's/^/   /'
if printf '%s\n' "$listing" | grep -q '^canary-'; then
  echo "FAIL something got through: a canary file exists"
  fail=1
else
  echo "PASS no canary file in the workspace"
fi
if printf '%s\n' "$listing" | grep -qx 'hello.txt'; then
  echo "PASS the file the agent was allowed to write is there, and wasn't deleted"
else
  echo "FAIL hello.txt is missing"
  fail=1
fi

echo "== the runner's record"
refusals="$(docker compose logs runner 2>/dev/null | grep -c '"msg":"runner: refused"' || true)"
echo "   refusals logged: ${refusals:-0}"

if [ "$fail" = 0 ]; then echo "ALL PASSED"; else echo "FAILED: see the FAIL lines above"; exit 1; fi
