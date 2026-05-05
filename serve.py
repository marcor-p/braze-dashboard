#!/usr/bin/env python3
"""
serve.py
========
Local Flask server for the Braze KPI dashboard.

Routes:
  GET /                  -> index.html
  GET /dashboard_data.json (or /api/data) -> latest dashboard data
  POST /api/refresh      -> re-run the extractor and return fresh data
  GET /api/status        -> last refresh timestamp + flow count

Run:
  pip install -r requirements.txt
  export BRAZE_API_KEY="..."
  export BRAZE_REST_ENDPOINT="https://rest.iad-05.braze.com"
  python serve.py            # serves on http://localhost:8000
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
import base64

print(f"[serve.py] Python: {sys.executable}", flush=True)
print(f"[serve.py] sys.path: {sys.path}", flush=True)

from flask import Flask, jsonify, send_file, request, abort

ROOT = Path(__file__).resolve().parent
DATA_PATH = Path(os.environ.get("BRAZE_OUT_DIR", str(ROOT))) / "dashboard_data.json"
PORT = int(os.environ.get("PORT", "8000"))
USE_SAMPLE = os.environ.get("USE_SAMPLE", "").lower() in ("1", "true", "yes")

app = Flask(__name__, static_folder=None)

# ----- Basic Auth -----
DASHBOARD_USERNAME = os.environ.get("DASHBOARD_USERNAME", "").strip()
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "").strip()
AUTH_ENABLED = bool(DASHBOARD_USERNAME and DASHBOARD_PASSWORD)


@app.before_request
def _require_auth():
    if not AUTH_ENABLED:
        return None  # auth disabled when env vars are unset
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        return _auth_challenge()
    try:
        decoded = base64.b64decode(auth[6:]).decode("utf-8", errors="replace")
        user, _, pw = decoded.partition(":")
    except Exception:
        return _auth_challenge()
    if user != DASHBOARD_USERNAME or pw != DASHBOARD_PASSWORD:
        return _auth_challenge()
    return None


def _auth_challenge():
    return (
        "Authentication required.",
        401,
        {"WWW-Authenticate": 'Basic realm="Braze Dashboard"'},
    )
# ----- /Basic Auth -----


refresh_lock = threading.Lock()
refresh_state = {"running": False, "last_run": None, "last_status": "idle", "last_error": None}


@app.route("/")
def root():
    return send_file(ROOT / "index.html")


@app.route("/dashboard_data.json")
@app.route("/api/data")
def get_data():
    if not DATA_PATH.exists():
        _run_sample()
    return send_file(DATA_PATH, mimetype="application/json")


@app.route("/api/status")
def status():
    info = dict(refresh_state)
    if DATA_PATH.exists():
        try:
            payload = json.loads(DATA_PATH.read_text())
            flows = payload.get("flows") or payload.get("canvases", [])
            info["generated_at"] = payload.get("generated_at")
            info["flows"] = len(flows)
            info["canvases"] = sum(1 for f in flows if f.get("flow_type") == "canvas") or len(flows)
            info["campaigns"] = sum(1 for f in flows if f.get("flow_type") == "campaign")
            info["rows"] = len(payload.get("rows", []))
            info["alerts"] = len(payload.get("alerts", []))
            info["endpoint"] = payload.get("endpoint")
        except Exception as e:
            info["error"] = str(e)
    return jsonify(info)


@app.route("/api/refresh", methods=["POST"])
def refresh():
    if refresh_state["running"]:
        return jsonify({"ok": False, "error": "refresh already running"}), 409
    use_sample = USE_SAMPLE or request.args.get("sample") == "1"
    threading.Thread(target=_do_refresh, args=(use_sample,), daemon=True).start()
    return jsonify({"ok": True, "started": True, "mode": "sample" if use_sample else "live"})


def _do_refresh(use_sample: bool) -> None:
    with refresh_lock:
        refresh_state.update({"running": True, "last_status": "running", "last_error": None})
        try:
            if use_sample:
                _run_sample()
            else:
                _run_extractor()
            refresh_state["last_status"] = "ok"
            refresh_state["last_run"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        except Exception as e:
            refresh_state["last_status"] = "error"
            refresh_state["last_error"] = str(e)
        finally:
            refresh_state["running"] = False


def _run_sample() -> None:
    out = subprocess.check_output([sys.executable, str(ROOT / "generate_sample_data.py")])
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_bytes(out)
    try:
        subprocess.check_call([
            sys.executable, str(ROOT / "alerts.py"),
            "--json", str(DATA_PATH), "--rewrite", "--quiet",
        ])
    except subprocess.CalledProcessError as e:
        print(f"  WARN: alerts.py failed: {e}", file=sys.stderr)


def _run_extractor() -> None:
    if not os.environ.get("BRAZE_API_KEY") or not os.environ.get("BRAZE_REST_ENDPOINT"):
        raise RuntimeError(
            "BRAZE_API_KEY and BRAZE_REST_ENDPOINT env vars are required for a live refresh. "
            "Either set them, or call /api/refresh?sample=1 to use synthetic data."
        )
    out_dir = os.environ.get("BRAZE_OUT_DIR", str(ROOT / "out"))
    env = dict(os.environ, BRAZE_OUT_DIR=out_dir)
    subprocess.check_call([sys.executable, str(ROOT / "braze_extract.py")], env=env)
    try:
        subprocess.check_call([sys.executable, str(ROOT / "alerts.py"), "--quiet"], env=env)
    except subprocess.CalledProcessError as e:
        print(f"  WARN: alerts.py failed: {e}", file=sys.stderr)
    src = Path(out_dir) / "dashboard_data.json"
    if not src.exists():
        raise RuntimeError(f"Extractor did not produce {src}")
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_bytes(src.read_bytes())


if __name__ == "__main__":
    print(f"\n  Braze dashboard → http://0.0.0.0:{PORT}", flush=True)
    print(f"  Mode: {'SAMPLE DATA' if USE_SAMPLE else 'LIVE (needs BRAZE_API_KEY + BRAZE_REST_ENDPOINT)'}\n", flush=True)
    app.run(host="0.0.0.0", port=PORT, debug=False)