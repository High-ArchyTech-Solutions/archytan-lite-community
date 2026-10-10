#!/bin/sh
# Archytan Lite checkpoint signer: HashiCorp Vault Transit.
# Tested: testing/checkpoints runs this file unchanged against a Vault dev
# server (TestCheckpoints_VaultTransit).
#
# Reads the message on stdin, signs it with an ecdsa-p256 Transit key, and
# prints the signature (ASN.1 DER, base64) for the gate.
#
# Gate settings:
#   ARCHYTAN_LITE_CHECKPOINT_SIGNER=["/usr/local/bin/vault-transit.sh"]
#   ARCHYTAN_LITE_CHECKPOINT_ALGORITHM=ecdsa-p256-sha256
#   ARCHYTAN_LITE_CHECKPOINT_PUBLIC_KEY_PATH=/etc/archytan/checkpoint.pub.pem
# and in the gate's environment, which the signer inherits:
#   VAULT_ADDR, VAULT_TOKEN (a token that can only sign; see below), and
#   CHECKPOINT_TRANSIT_KEY if the key isn't named archytan-checkpoint.
#
# One-time setup, as a Vault admin:
#   vault secrets enable transit
#   vault write -f transit/keys/archytan-checkpoint type=ecdsa-p256
#   vault read -format=json transit/keys/archytan-checkpoint \
#     | jq -r '.data.keys."1".public_key' > checkpoint.pub.pem
#   vault policy write archytan-checkpoint - <<'EOF'
#   path "transit/sign/archytan-checkpoint/sha2-256" {
#     capabilities = ["update"]
#   }
#   EOF
#   vault token create -policy=archytan-checkpoint -field=token
#
# Turn on a Vault audit device (vault audit enable file ...) so every
# signature the gate host asks for is on record.
set -eu
key=${CHECKPOINT_TRANSIT_KEY:-archytan-checkpoint}
input=$(base64 | tr -d '\n')
signature=$(vault write -field=signature "transit/sign/$key/sha2-256" input="$input")
# Transit answers vault:v<key version>:<base64>.
printf '%s\n' "${signature#vault:v*:}"
