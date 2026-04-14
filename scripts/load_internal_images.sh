#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

docker load -i artifacts/images/shinhan_gateway.tar
docker load -i artifacts/images/shinhan_nemotron_asr.tar
