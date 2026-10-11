#!/bin/sh
# Archytan Lite checkpoint signer: an HSM through PKCS #11, with OpenSC's
# pkcs11-tool (0.15 or later) and openssl.
# Tested against a SoftHSM2 2.6.1 token with OpenSC 0.27: testing/checkpoints
# (TestCheckpoints_PKCS11) runs it unchanged as a gate's signer. Not yet run
# against a hardware HSM, so check it with your own token before you rely
# on it.
#
# Reads the message on stdin, hashes it with SHA-256, has the token sign the
# digest with an EC P-256 key (mechanism ECDSA), and prints the signature
# (ASN.1 DER, base64) for the gate. That's an ECDSA-SHA256 signature of the
# message, made with the plain ECDSA mechanism (CKM_ECDSA), so it also works
# on tokens with no ECDSA-SHA256 mechanism, SoftHSM2 2.6 among them.
#
# Gate settings:
#   ARCHYTAN_LITE_CHECKPOINT_SIGNER=["/usr/local/bin/pkcs11.sh"]
#   ARCHYTAN_LITE_CHECKPOINT_ALGORITHM=ecdsa-p256-sha256
#   ARCHYTAN_LITE_CHECKPOINT_PUBLIC_KEY_PATH=/etc/archytan/checkpoint.pub.pem
# and in the gate's environment, which the signer inherits:
#   PKCS11_MODULE (your HSM vendor's PKCS #11 library), PKCS11_KEY_ID (the
#   key's CKA_ID, in hex) and PKCS11_PIN. pkcs11-tool reads the PIN from the
#   environment, so it never appears in the process list.
#
# Export the public key once:
#   pkcs11-tool --module <library> --read-object --type pubkey --id <id> \
#     --output-file checkpoint.pub.der
#   openssl pkey -pubin -inform DER -in checkpoint.pub.der -out checkpoint.pub.pem
#
# Check that your HSM's own audit log records each signature.
set -eu
: "${PKCS11_PIN:?PKCS11_PIN is not set}"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cat > "$work/message"
openssl dgst -sha256 -binary -out "$work/digest" "$work/message"
pkcs11-tool --module "$PKCS11_MODULE" --login --pin env:PKCS11_PIN \
  --sign --mechanism ECDSA --id "$PKCS11_KEY_ID" \
  --signature-format openssl \
  --input-file "$work/digest" --output-file "$work/signature" >&2
base64 < "$work/signature" | tr -d '\n'
echo
