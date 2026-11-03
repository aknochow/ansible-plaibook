#!/usr/bin/env bash
# Verify Red Hat's signature on the base image execution-environment.yml
# pins. The key is Red Hat release key 3, from
# https://security.access.redhat.com/data/63405576.txt, vendored as
# execution-environment-redhat-key.pem.
#
# --insecure-ignore-tlog: Red Hat's signatures carry Rekor bundles whose
# log key is not in the public Sigstore trusted root, so cosign cannot
# check log inclusion. The signature over the digest is checked against
# the vendored key.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
base="$(sed -n 's/^ *name: \(registry\.access\.redhat\.com\/[^@ ]*@sha256:[0-9a-f]\{64\}\)$/\1/p' "${root}/execution-environment.yml")"
if [ -z "$base" ]; then
  echo "no digest-pinned registry.access.redhat.com base in execution-environment.yml" >&2
  exit 1
fi
image="${base%%:*}"
digest="${base##*@}"
cosign verify \
  --key "${root}/execution-environment-redhat-key.pem" \
  --insecure-ignore-tlog \
  "${image}@${digest}" > /dev/null
echo "verified Red Hat signature on ${base}"
