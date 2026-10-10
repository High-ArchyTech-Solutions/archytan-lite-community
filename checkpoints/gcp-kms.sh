#!/bin/sh
# Archytan Lite checkpoint signer: Google Cloud KMS.
# Untested: written from the gcloud documentation and never run against
# Google Cloud. Check it with your own key before you rely on it.
#
# Reads the message on stdin, signs it with an asymmetric signing key
# (algorithm EC_SIGN_P256_SHA256), and prints the signature (ASN.1 DER,
# base64) for the gate.
#
# Gate settings:
#   ARCHYTAN_LITE_CHECKPOINT_SIGNER=["/usr/local/bin/gcp-kms.sh"]
#   ARCHYTAN_LITE_CHECKPOINT_ALGORITHM=ecdsa-p256-sha256
#   ARCHYTAN_LITE_CHECKPOINT_PUBLIC_KEY_PATH=/etc/archytan/checkpoint.pub.pem
# and in the gate's environment, which the signer inherits:
#   CHECKPOINT_KMS_LOCATION, CHECKPOINT_KMS_KEYRING, CHECKPOINT_KMS_KEY and
#   CHECKPOINT_KMS_KEY_VERSION, and credentials for a service account that
#   holds roles/cloudkms.signer on that key and nothing else.
#
# One-time setup:
#   gcloud kms keys create <key> --keyring <keyring> --location <location> \
#     --purpose asymmetric-signing --default-algorithm ec-sign-p256-sha256
#   gcloud kms keys versions get-public-key 1 --key <key> --keyring <keyring> \
#     --location <location> --output-file checkpoint.pub.pem
#
# With Data Access audit logs turned on for Cloud KMS, Cloud Audit Logs
# records every AsymmetricSign call.
set -eu
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cat > "$work/message"
gcloud kms asymmetric-sign --location "$CHECKPOINT_KMS_LOCATION" \
  --keyring "$CHECKPOINT_KMS_KEYRING" --key "$CHECKPOINT_KMS_KEY" \
  --version "$CHECKPOINT_KMS_KEY_VERSION" --digest-algorithm sha256 \
  --input-file "$work/message" --signature-file "$work/signature" >&2
base64 < "$work/signature" | tr -d '\n'
echo
