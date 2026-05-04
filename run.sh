#!/usr/bin/env bash
# run.sh — start the Braze dashboard locally on http://localhost:8000
#
# Usage:
#   ./run.sh                  # serve current data; sample-generates if missing
#   ./run.sh --sample         # regenerate sample data + run alerts, then serve
#   ./run.sh --extract        # run live extractor + alerts before serving
#   ./run.sh --static         # plain http.server, no Flask, no refresh button
#   ./run.sh --slack          # also post Slack digest after alerts run
#
# Live extraction needs:
#   BRAZE_API_KEY="..."
#   BRAZE_REST_ENDPOINT="https://rest.iad-05.braze.com"
# Slack digest needs:
#   SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."

set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 not found. Install Python 3.9+ and retry." >&2; exit 1
fi

if [[ ! -d ".venv" ]]; then
  echo "→ Creating .venv ..."
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

if ! python -c "import flask, requests" 2>/dev/null; then
  echo "→ Installing dependencies ..."
  pip install --quiet --upgrade pip
  pip install --quiet -r requirements.txt
fi

MODE="flask"
DO_EXTRACT=0
DO_SAMPLE=0
DO_SLACK=0
for arg in "$@"; do
  case "$arg" in
    --sample) DO_SAMPLE=1 ;;
    --extract) DO_EXTRACT=1 ;;
    --static) MODE="static" ;;
    --slack) DO_SLACK=1 ;;
    *) ;;
  esac
done

if [[ "$DO_EXTRACT" == "1" ]]; then
  echo "→ Running live extractor ..."
  python braze_extract.py
  cp out/dashboard_data.json dashboard_data.json
  echo "→ Running alerts ..."
  python alerts.py
  cp out/dashboard_data.json dashboard_data.json
fi

if [[ "$DO_SAMPLE" == "1" ]]; then
  echo "→ Regenerating sample data ..."
  python generate_sample_data.py > dashboard_data.json
  echo "→ Running alerts against sample ..."
  python alerts.py --json dashboard_data.json --rewrite
fi

if [[ "$DO_SLACK" == "1" ]]; then
  echo "→ Posting Slack digest ..."
  python alerts.py --slack --quiet
fi

if [[ ! -f "dashboard_data.json" ]]; then
  echo "→ No dashboard_data.json, generating sample ..."
  python generate_sample_data.py > dashboard_data.json
  python alerts.py --json dashboard_data.json --rewrite --quiet || true
fi

echo
echo "  ┌──────────────────────────────────────────────────────────┐"
echo "  │  Braze KPI Dashboard                                     │"
echo "  │  → http://localhost:${PORT}                                  │"
echo "  │  Mode: ${MODE}"
echo "  │  Press Ctrl-C to stop                                    │"
echo "  └──────────────────────────────────────────────────────────┘"
echo

if [[ "$MODE" == "static" ]]; then
  exec python -m http.server "$PORT"
else
  exec python serve.py
fi
