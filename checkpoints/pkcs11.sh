#!/bin/sh
# Archytan Lite checkpoint signer: an HSM through PKCS #11, with OpenSC's
# pkcs11-tool.
# Untested: written from the pkcs11-tool documentation and never run against
# an HSM. Check it with your own token before you rely on it.
#
# Reads the message on stdin, signs it with an EC P-256 key on the token
# (mechanism ECDSA-SHA256), and prints the signature (ASN.1 DER, base64) for
# the gate.
#
# Gate settings:
#   ARCHYTAN_LITE_CHECKPOINT_SIGNER=["/usr/local/bin/pkcs11.sh"]
#   ARCHYTAN_LITE_CHECKPOINT_ALGORITHM=ecdsa-p256-sha256
#   ARCHYTAN_LITE_CHECKPOINT_PUBLIC_KEY_PATH=/etc/archytan/checkpoint.pub.pem
# and in the gate's environment, which the signer inherits:
#   PKCS11_MODULE (your HSM vendor's PKCS #11 library), PKCS11_KEY_ID (the
#   key's CKA_ID, in hex) and PKCS11_PIN. The PIN is passed on the command
#   line, where other users on the same host could see it in the process
#   list; run the gate where nothing else does.
#
# Export the public key once:
#   pkcs11-tool --module <library> --read-object --type pubkey --id <id> \
#     --output-file checkpoint.pub.der
#   openssl pkey -pubin -inform DER -in checkpoint.pub.der -out checkpoint.pub.pem
#
# Check that your HSM's own audit log records each signature.
set -eu
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cat > "$work/message"
pkcs11-tool --module "$PKCS11_MODULE" --login --pin "$PKCS11_PIN" \
  --sign --mechanism ECDSA-SHA256 --id "$PKCS11_KEY_ID" \
  --signature-format openssl \
  --input-file "$work/message" --output-file "$work/signature" >&2
base64 < "$work/signature" | tr -d '\n'
echo
