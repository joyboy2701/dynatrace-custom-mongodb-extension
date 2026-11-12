#!/usr/bin/env bash
# Build extension for Linux ActiveGate (x86_64 and aarch64).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/custom_integration"

SDK_PYTHON="$ROOT/.venv/bin/python"

if ! "$SDK_PYTHON" -c 'import dynatrace_extension.cli' >/dev/null 2>&1; then
  echo "Installing dt-extensions-sdk in .venv ..."
  "$SDK_PYTHON" -m pip install "dt-extensions-sdk[cli]"
fi

if [[ ! -f "$HOME/.dynatrace/certificates/developer.pem" ]]; then
  echo "Run: dt-sdk gencerts"
  exit 1
fi

echo "Building for ActiveGate (Linux x86_64/aarch64; CPython 3.10/3.14) ..."
./build.sh

echo "Done:"
ls -la dist/custom_mongodb-query-*.zip 2>/dev/null | tail -3
