#!/usr/bin/env bash
# One-time setup for the gated runner example: the gate's signing key, and
# two credentials, one for the agent (role assistant) and one for the
# runner (role runner, which the policy grants nothing). Safe to run again:
# it keeps what exists.
#
#   ./setup.sh                                  with the published image
#   LITE_IMAGE=archytan-lite:dev ./setup.sh     with an image you built
#
# Writes to STATE_DIR (default ./secrets): agent.token, runner.token,
# callers/callers.json, and public.key (the gate's, for checking receipts).
# The signing key goes in the Compose project's gate-data volume.
set -euo pipefail
export MSYS_NO_PATHCONV=1 # Git Bash on Windows: pass /data/... to Docker as written
cd "$(dirname "$0")"
LITE_IMAGE="${LITE_IMAGE:-ghcr.io/high-archytech-solutions/archytan-lite:2.9.0}"
project="${COMPOSE_PROJECT_NAME:-archytan-gated-runner}"
state="${STATE_DIR:-./secrets}"
volume="${project}_gate-data"

mkdir -p "$state/callers"
if ! docker volume inspect "$volume" >/dev/null 2>&1; then
  echo "== gate signing key"
  docker volume create --label "com.docker.compose.project=$project" \
    --label com.docker.compose.volume=gate-data "$volume" >/dev/null
  out="$(docker run --rm -v "$volume:/data" --entrypoint keygen "$LITE_IMAGE" -out /data/signing_key.pem)"
  printf '%s\n' "$out"
  # Keep the public key: checking the receipt chain later needs it.
  printf '%s\n' "$out" | sed -n 's/^public key.*: *\([0-9a-f]\{64\}\)$/\1/p' >"$state/public.key"
  [ -s "$state/public.key" ] || { echo "keygen printed no public key" >&2; exit 1; }
fi
if [ ! -s "$state/public.key" ]; then
  # The key was made on an earlier run and its public half lost: read it
  # back from the signing key itself.
  echo "== gate public key (from the signing key)"
  docker run --rm -v "$volume:/data:ro" --entrypoint keygen "$LITE_IMAGE" -public-of /data/signing_key.pem >"$state/public.key"
fi

record() { # caller-id role token-file -> prints the callers-file record
  local j
  j="$(docker run --rm --entrypoint callergen "$LITE_IMAGE" -caller-id "$1" -role "$2" -json)"
  printf '%s\n' "$j" | sed -n 's/.*"token":"\([^"]*\)".*/\1/p' >"$3"
  printf '%s\n' "$j" | sed -n 's/.*"record":\({[^}]*}\).*/\1/p'
}

if [ ! -s "$state/agent.token" ] || [ ! -s "$state/runner.token" ] || [ ! -s "$state/callers/callers.json" ]; then
  echo "== credentials: agent (role assistant), runner (role runner)"
  agent="$(record agent assistant "$state/agent.token")"
  runner="$(record runner runner "$state/runner.token")"
  printf '{"version":1,"callers":[%s,%s]}\n' "$agent" "$runner" >"$state/callers/callers.json"
  # Compose mounts the tokens into containers that run as other users. In a
  # real deployment keep them in your secret store instead.
  chmod 644 "$state"/*.token
  # The gate reads the callers file, and the runner its token, only when
  # they start; and a running agent keeps the token file it was started
  # with if the file was made anew.
  if [ -n "$(docker compose ps -q gate 2>/dev/null)" ]; then
    echo "== restarting the gate, the runner and the agent for the new credentials"
    docker compose restart gate >/dev/null
    docker compose restart runner >/dev/null
    docker compose restart agent >/dev/null
  fi
fi
echo "ready: docker compose up -d --wait"
