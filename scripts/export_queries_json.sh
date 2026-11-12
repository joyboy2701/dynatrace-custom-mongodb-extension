#!/usr/bin/env bash
# Print queries.json as a single line for Dynatrace "Advanced: queries JSON" field.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python3 -c "import json; print(json.dumps(json.load(open('$ROOT/custom_integration/queries.json')))"
