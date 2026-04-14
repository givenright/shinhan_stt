#!/usr/bin/env bash
set -euo pipefail

IMAGE="${1:-nvcr.io/nvidia/nemo:25.11}"
OUT="${2:-artifacts/images/nemo_25_11.tar}"

mkdir -p "$(dirname "$OUT")"
docker pull "$IMAGE"
docker save -o "$OUT" "$IMAGE"
echo "$OUT"
