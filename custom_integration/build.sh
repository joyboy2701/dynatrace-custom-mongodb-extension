#!/usr/bin/env bash
# Build for ActiveGate (Linux). Run from custom_integration/ or project root.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SDK_PYTHON="${ROOT}/../.venv/bin/python"
if [[ ! -x "$SDK_PYTHON" ]]; then
  SDK_PYTHON="${ROOT}/.venv/bin/python"
fi
export PATH="$(dirname "$SDK_PYTHON"):${PATH}"
cd "$ROOT"
# Bundle both Linux CPU architectures; the build host may differ from ActiveGate.
# Keep CPython 3.10 support and explicitly include ActiveGate's CPython 3.14.
# The SDK defaults to Python 3.10 when --python-version is omitted.
exec "$SDK_PYTHON" -c 'from dynatrace_extension.cli import app; app()' build --extra-platform manylinux2014_x86_64 --extra-platform manylinux2014_aarch64 --only-extra-platforms --python-version 3.10 --python-version 3.14 "$@"
