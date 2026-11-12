#!/usr/bin/env bash
# Upload the signed extension zip to Dynatrace via dt-sdk.
#
# Requires .env (copy from .env.example) with:
#   DT_ENV_URL    e.g. https://xxxxx.live.dynatrace.com
#   DT_API_TOKEN  API access token, scopes: extensions.write (+ extensions.read)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a && source .env && set +a
fi

: "${DT_ENV_URL:?Set DT_ENV_URL in .env (e.g. https://xxxxx.live.dynatrace.com)}"
: "${DT_API_TOKEN:?Set DT_API_TOKEN in .env (scopes: extensions.write, extensions.read)}"

if [[ "$DT_ENV_URL" == *".apps.dynatrace.com"* ]]; then
  echo "DT_ENV_URL points to apps.dynatrace.com. Use the environment URL, e.g. https://xxxxx.live.dynatrace.com" >&2
  exit 1
fi

# dt-sdk reads DT_API_URL for the target environment.
export DT_API_URL="$DT_ENV_URL"

DT_SDK="$ROOT/.venv/bin/dt-sdk"
[[ -x "$DT_SDK" ]] || DT_SDK="dt-sdk"

ZIP="$(ls -t "$ROOT"/custom_integration/dist/custom_mongodb-query-*.zip 2>/dev/null | head -1 || true)"
if [[ -z "$ZIP" ]]; then
  echo "No built zip found in custom_integration/dist/." >&2
  echo "Run ./scripts/build_custom_integration.sh first." >&2
  exit 1
fi

echo "Uploading $(basename "$ZIP") to $DT_API_URL ..."
exec "$DT_SDK" upload "$ZIP"
