#!/usr/bin/env bash
# Regenerate plaibook/hashed/*-requirements.txt with hashes for every
# transitive dependency. Runtime installers use
# `pip install --require-hashes -r` and must not resolve ranges.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

compile_310() {
  local name="$1"
  uv pip compile --generate-hashes --python-version 3.10 --no-annotate \
    "plaibook/hashed/${name}.in" -o "plaibook/hashed/${name}-requirements.txt"
}

compile_310 openai
compile_310 claude
compile_310 gemini
compile_310 cursor
compile_310 xai

uv pip compile --generate-hashes --python-version 3.11 --no-annotate \
  plaibook/hashed/openshell.in -o plaibook/hashed/openshell-requirements.txt

uv pip compile --generate-hashes --python-version 3.11 --no-annotate \
  plaibook/hashed/sandbox-runtime.in -o plaibook/hashed/sandbox-runtime-requirements.txt

uv pip compile --generate-hashes --python-version 3.11 --no-annotate \
  plaibook/hashed/build-backend.in -o plaibook/hashed/build-backend-requirements.txt
