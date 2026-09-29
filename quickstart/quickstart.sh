#!/usr/bin/env bash
# Archytan Lite in about a minute: start a gate, get a signed ALLOW and a
# BLOCK, spend a single-use capability twice, and verify the decision log.
#
#   bash quickstart.sh
#
# Needs Docker and curl. The gate listens on 127.0.0.1 only, so the
# example token below never leaves this machine. Every answer is checked
# as it arrives, so the script stops at the first one that differs from
# what the comments say.
#
# This file is published unchanged in
# github.com/High-ArchyTech-Solutions/archytan-lite-community, and CI runs
# it against every image before release (scripts/quickstart-check.sh).
set -euo pipefail
export MSYS_NO_PATHCONV=1 # Git Bash on Windows: pass /data paths to Docker as written

IMAGE="${IMAGE:-ghcr.io/high-archytech-solutions/archytan-lite:2.2.1}"
NAME=archytan-quickstart
GATE=http://127.0.0.1:8421
AUTH="Authorization: Bearer quickstart-token"
JSON="Content-Type: application/json"

expect() { # expect <text> <answer>: stop unless the answer contains the text
  case "$2" in *"$1"*) ;; *) echo "unexpected answer, wanted $1: $2" >&2; exit 1 ;; esac
}

if docker volume inspect "$NAME" >/dev/null 2>&1; then
  echo "Remove the previous run first: docker rm -f $NAME && docker volume rm $NAME" >&2
  exit 1
fi

echo "== 1. Create a volume for the signing key and the decision log"
docker volume create "$NAME" >/dev/null

echo "== 2. Generate the gate's signing key; its public half verifies every receipt"
PUBKEY=$(docker run --rm -v "$NAME:/data" --entrypoint keygen "$IMAGE" \
  -out /data/signing_key.pem | sed -n 's/^public key.*: //p')
echo "public key: $PUBKEY"

echo "== 3. Start the gate with the example policy and single-use capabilities"
docker run -d --name "$NAME" -p 127.0.0.1:8421:8421 -v "$NAME:/data" \
  -e ARCHYTAN_LITE_CALLER_TOKEN=quickstart-token \
  -e ARCHYTAN_LITE_DB_PATH=/data/archytan.db \
  -e ARCHYTAN_LITE_SIGNING_KEY_PATH=/data/signing_key.pem \
  -e ARCHYTAN_LITE_POLICY_PATH=/usr/local/share/archytan-lite/examples/policy.json \
  -e ARCHYTAN_LITE_INSTANCE_URN=urn:archytan-lite:instance:quickstart \
  -e ARCHYTAN_LITE_CAPABILITIES=on \
  "$IMAGE" >/dev/null
for _ in $(seq 60); do curl -sf "$GATE/v1/healthz" >/dev/null && break; sleep 0.5; done
curl -sf "$GATE/v1/healthz"; echo

echo "== 4. An admin asks to delete business biz_42: ALLOW, a signed receipt and a capability"
ALLOW=$(curl -s "$GATE/v1/authorize" -H "$AUTH" -H "$JSON" -d '{
  "action": "business.delete",
  "actor": {"uid": "u1", "role": "admin"},
  "resource": {"type": "business", "id": "biz_42"},
  "idempotency_key": "quickstart-1"}')
echo "$ALLOW"; expect '"decision":"ALLOW"' "$ALLOW"

echo "== 5. An owner asks for the same thing: BLOCK, and the refusal is logged too"
BLOCK=$(curl -s "$GATE/v1/authorize" -H "$AUTH" -H "$JSON" -d '{
  "action": "business.delete",
  "actor": {"uid": "u2", "role": "owner"},
  "resource": {"type": "business", "id": "biz_42"},
  "idempotency_key": "quickstart-2"}')
echo "$BLOCK"; expect '"decision":"BLOCK"' "$BLOCK"

echo "== 6. Spend the capability right before acting: the first redeem works, a second is refused"
CAP=$(echo "$ALLOW" | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')
REDEEM='{"token": "'"$CAP"'", "action": "business.delete", "resource_type": "business", "resource_id": "biz_42"}'
FIRST=$(curl -s "$GATE/v1/redeem" -H "$AUTH" -H "$JSON" -d "$REDEEM")
echo "$FIRST"; expect '"decision":"ALLOW"' "$FIRST"
SECOND=$(curl -s "$GATE/v1/redeem" -H "$AUTH" -H "$JSON" -d "$REDEEM")
echo "$SECOND"; expect '"decision":"BLOCK"' "$SECOND"

echo "== 7. Verify the log: every receipt signed, none edited, reordered or removed"
VERIFY=$(docker exec -e ARCHYTAN_LITE_TRUSTED_PUBLIC_KEYS_HEX="$PUBKEY" "$NAME" archytan-lite --verify-chain)
echo "$VERIFY"; expect "chain OK: 2 receipt(s)" "$VERIFY"

echo
echo "Done. Clean up with: docker rm -f $NAME && docker volume rm $NAME"
