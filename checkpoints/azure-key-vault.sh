#!/bin/sh
# Archytan Lite checkpoint signer: Azure Key Vault.
# Untested: written from the Azure CLI documentation and never run against
# Azure. Check it with your own key before you rely on it.
#
# Reads the message on stdin, signs its SHA-256 digest with an EC P-256 key
# (ES256), and prints the signature for the gate. Key Vault returns ECDSA
# signatures as raw r||s rather than DER; the gate converts them.
#
# Gate settings:
#   ARCHYTAN_LITE_CHECKPOINT_SIGNER=["/usr/local/bin/azure-key-vault.sh"]
#   ARCHYTAN_LITE_CHECKPOINT_ALGORITHM=ecdsa-p256-sha256
#   ARCHYTAN_LITE_CHECKPOINT_PUBLIC_KEY_PATH=/etc/archytan/checkpoint.pub.pem
# and in the gate's environment, which the signer inherits:
#   CHECKPOINT_KEY_VAULT and CHECKPOINT_KEY_NAME, signed in (az login, or a
#   managed identity) as an identity allowed to sign with that key and
#   nothing else.
#
# One-time setup:
#   az keyvault key create --vault-name <vault> --name <key> \
#     --kty EC --curve P-256 --ops sign verify
#   az keyvault key download --vault-name <vault> --name <key> \
#     --encoding PEM --file checkpoint.pub.pem
#
# Turn on the vault's AuditEvent diagnostic logs to record every sign call.
set -eu
digest=$(openssl dgst -sha256 -binary | base64 | tr -d '\n')
az keyvault key sign --vault-name "$CHECKPOINT_KEY_VAULT" --name "$CHECKPOINT_KEY_NAME" \
  --algorithm ES256 --digest "$digest" --query signature --output tsv
