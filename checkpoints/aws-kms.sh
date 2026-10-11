#!/bin/sh
# Archytan Lite checkpoint signer: AWS KMS.
# Tested against moto 5.2.3's KMS emulation with AWS CLI 2.37:
# testing/checkpoints (TestCheckpoints_AWSKMS) runs it unchanged as a gate's
# signer. Not yet run against AWS itself, so check it with your own key
# before you rely on it.
#
# Reads the message on stdin, signs it with an asymmetric KMS key (key spec
# ECC_NIST_P256, key usage SIGN_VERIFY), and prints the signature (ASN.1
# DER, base64) for the gate.
#
# Gate settings:
#   ARCHYTAN_LITE_CHECKPOINT_SIGNER=["/usr/local/bin/aws-kms.sh"]
#   ARCHYTAN_LITE_CHECKPOINT_ALGORITHM=ecdsa-p256-sha256
#   ARCHYTAN_LITE_CHECKPOINT_PUBLIC_KEY_PATH=/etc/archytan/checkpoint.pub.pem
# and in the gate's environment, which the signer inherits:
#   CHECKPOINT_KMS_KEY_ID (key id, ARN or alias), and credentials for a role
#   allowed kms:Sign on that key and nothing else.
#
# One-time setup:
#   aws kms create-key --key-spec ECC_NIST_P256 --key-usage SIGN_VERIFY
#   aws kms get-public-key --key-id <key> --query PublicKey --output text \
#     | base64 -d | openssl pkey -pubin -inform DER -out checkpoint.pub.pem
#
# CloudTrail records every kms:Sign call.
set -eu
message=$(mktemp)
trap 'rm -f "$message"' EXIT
cat > "$message"
aws kms sign --key-id "$CHECKPOINT_KMS_KEY_ID" --message "fileb://$message" \
  --message-type RAW --signing-algorithm ECDSA_SHA_256 \
  --query Signature --output text
