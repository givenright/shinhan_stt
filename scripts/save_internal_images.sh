#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

mkdir -p artifacts/images
docker save -o artifacts/images/shinhan_gateway.tar shinhan/gateway:latest
docker save -o artifacts/images/shinhan_nemotron_asr.tar shinhan/nemotron-asr:latest
