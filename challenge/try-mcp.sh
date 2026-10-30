#!/usr/bin/env bash
# The Archytan Lite MCP challenge: an agent behind the MCP gate, then the
# attacks, with nothing installed but Docker.
#
#   bash try-mcp.sh
#
# It starts an Archytan Lite gate with credential-bound roles and single-use
# capabilities, puts the reference MCP filesystem server behind the MCP
# gate, drives an agent session through it, and makes the attacks the
# challenge names against the gate's API. Every answer is checked as it
# arrives, and the script stops at the first one that differs from what it
# expects. The gate keeps running afterwards, on 127.0.0.1 only, for your
# own attempts.
#
# The first run downloads the gate image, node:22-slim, the MCP gate and the
# filesystem server. Run on its own, it fetches challenge.mjs with curl; run
# from a clone, it uses the copy beside it. Same steps as try-mcp.ps1.
set -euo pipefail
export MSYS_NO_PATHCONV=1 # Git Bash on Windows: pass container paths to Docker as written

IMAGE="${IMAGE:-ghcr.io/high-archytech-solutions/archytan-lite:2.7.1}"
NODE_IMAGE="${NODE_IMAGE:-node:22-slim}"
NAME=archytan-mcp-challenge
WORK="$NAME-work"
PORT="${PORT:-8431}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-.}")" && pwd)"
SOURCE=https://raw.githubusercontent.com/High-ArchyTech-Solutions/archytan-lite-community/main/challenge/challenge.mjs

docker info >/dev/null 2>&1 || { echo "Docker isn't running. Start it and run this again." >&2; exit 1; }
if docker container inspect "$NAME" >/dev/null 2>&1 || docker volume inspect "$NAME" >/dev/null 2>&1 \
  || docker volume inspect "$WORK" >/dev/null 2>&1; then
  echo "Remove the previous run first: docker rm -f $NAME; docker volume rm $NAME $WORK" >&2
  exit 1
fi

echo "== 1. Volumes for the gate's data and the challenge's files, and the gate's signing key"
docker volume create "$NAME" >/dev/null
docker volume create "$WORK" >/dev/null
PUBKEY=$(docker run --rm -v "$NAME:/data" --entrypoint keygen "$IMAGE" \
  -out /data/signing_key.pem | sed -n 's/^public key.*: //p')
echo "  public key: $PUBKEY"

echo "== 2. A credential for the agent, bound to the role files_agent, and the config"
if [ -f "$HERE/challenge.mjs" ]; then SCRIPT=$(cat "$HERE/challenge.mjs"); else SCRIPT=$(curl -fsSL "$SOURCE"); fi
printf '%s\n' "$SCRIPT" | docker run --rm -i -v "$WORK:/challenge" "$NODE_IMAGE" sh -c 'cat > /challenge/challenge.mjs'
docker run --rm --entrypoint callergen "$IMAGE" -caller-id challenge-agent -role files_agent \
  | docker run --rm -i -v "$WORK:/challenge" "$NODE_IMAGE" node /challenge/challenge.mjs setup | sed 's/^/  /'

echo "== 3. Start the gate: credential-bound roles, single-use capabilities, 127.0.0.1 only"
docker run -d --name "$NAME" -p "127.0.0.1:$PORT:8421" -v "$NAME:/data" -v "$WORK:/challenge:ro" \
  -e ARCHYTAN_LITE_CALLERS_PATH=/challenge/config/callers.json \
  -e ARCHYTAN_LITE_POLICY_PATH=/challenge/config/policy.json \
  -e ARCHYTAN_LITE_DB_PATH=/data/archytan.db \
  -e ARCHYTAN_LITE_SIGNING_KEY_PATH=/data/signing_key.pem \
  -e ARCHYTAN_LITE_INSTANCE_URN=urn:archytan-lite:instance:mcp-challenge \
  -e ARCHYTAN_LITE_CAPABILITIES=on \
  "$IMAGE" >/dev/null
for _ in $(seq 60); do docker exec "$NAME" archytan-lite -healthcheck >/dev/null 2>&1 && break; sleep 0.5; done
docker exec "$NAME" archytan-lite -healthcheck >/dev/null || { docker logs "$NAME" >&2; exit 1; }
echo "  listening on 127.0.0.1:$PORT"

# 4 to 6 run in a node container that shares the gate's network, so the MCP
# gate reaches it on loopback, and its process namespace, so the policy
# reload can signal it.
docker run --rm --network "container:$NAME" --pid "container:$NAME" -v "$WORK:/challenge" \
  -e PUBKEY="$PUBKEY" -e npm_config_cache=/challenge/.npm "$NODE_IMAGE" node /challenge/challenge.mjs run

echo
echo "== 7. The gate's own log says why each attack was refused, and the decision log verifies"
REASONS=$(docker logs "$NAME" 2>&1 | grep -E "escalation attempt refused|outside every grant|more than one way|does not authorize|already spent|would not be made now" \
  | sed -E 's/^[0-9\/]+ [0-9:]+ (INFO|WARN) //; s/ trace_id=[^ ]+//; s/ cap_id=[^ ]+//' || true)
echo "$REASONS" | sed 's/^/  /'
for want in "escalation attempt refused" "resource outside every grant" "could be read more than one way" \
  "does not authorize this action" "already spent" "would not be made now"; do
  case "$REASONS" in *"$want"*) ;; *) echo "unexpected: the gate's log has no \"$want\"" >&2; exit 1 ;; esac
done
VERIFY=$(docker exec -e ARCHYTAN_LITE_TRUSTED_PUBLIC_KEYS_HEX="$PUBKEY" "$NAME" archytan-lite --verify-chain)
echo "  $VERIFY"
case "$VERIFY" in "chain OK"*) ;; *) echo "unexpected answer, wanted chain OK: $VERIFY" >&2; exit 1 ;; esac

TOKEN=$(docker run --rm -v "$WORK:/challenge" "$NODE_IMAGE" cat /challenge/config/agent.token)
cat <<EOF

== Your turn

The gate is still running on 127.0.0.1:$PORT with the same policy. The agent's
credential holds the role files_agent:

  $TOKEN

Try to get past it: claim another role, read outside /challenge/files, spend
a capability on something it wasn't issued for, spend one twice or after the
policy changed, or build an MCP message the MCP gate and the server read
differently. For example:

  curl -s http://127.0.0.1:$PORT/v1/authorize -H "Authorization: Bearer $TOKEN" \\
    -H "Content-Type: application/json" -d '{"action":"file.write",
    "actor":{"uid":"you","role":"admin"},"resource":{"type":"file","id":"/challenge/files/readme.txt"},
    "idempotency_key":"try-1"}'

More attacks to try, and how to put your own MCP host in front of it:
  https://github.com/High-ArchyTech-Solutions/archytan-lite-community/tree/main/challenge

If something gets through, report it privately, never in a public issue:
  the MCP gate:  https://github.com/High-ArchyTech-Solutions/archytan-mcp-gate/security/advisories/new
  the gate:      https://github.com/High-ArchyTech-Solutions/archytan-lite-community/security/advisories/new
Tell us how it went, whatever happened:
  https://github.com/High-ArchyTech-Solutions/archytan-lite-community/issues/new?template=challenge-report.yml

Clean up with: docker rm -f $NAME; docker volume rm $NAME $WORK
EOF
