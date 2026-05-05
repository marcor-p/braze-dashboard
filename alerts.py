#!/usr/bin/env python3
"""
alerts.py
=========
Reads message_metrics_daily and writes flagged regressions to the `alerts`
table + dashboard JSON. Optionally posts a daily digest to Slack.

Usage (after braze_extract.py has run):
  python alerts.py                 # writes to out/braze_metrics.db + dashboard JSON
  python alerts.py --json out/dashboard_data.json --rewrite   # runs against the JSON only
                                                              # (sample-data path)
  python alerts.py --slack         # also posts digest to Slack

Env vars:
  SLACK_WEBHOOK_URL   Incoming webhook for the digest (required for --slack)
  BRAZE_OUT_DIR       Output dir (default ./out, must contain braze_metrics.db)

Tuning thresholds: edit the RULES block below.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

# ----- Threshold loading (UI-editable via alert_config.json) -----
DEFAULT_THRESHOLDS = {
    "email_delivery_min": 0.95,
    "email_open_drop_pp": -5.0,
    "email_click_drop_pp": -2.0,
    "email_unsub_max": 0.005,
    "email_spam_max": 0.001,
    "push_delivery_min": 0.97,
    "push_open_drop_pp": -3.0,
    "sms_delivery_min": 0.98,
    "sms_optout_max": 0.01,
    "all_volume_collapse_pct": 0.50,
    "all_min_sample": 1000,
}

def load_thresholds():
    out_dir = Path(os.environ.get("BRAZE_OUT_DIR", "./out")).resolve()
    candidates = [out_dir / "alert_config.json", Path(__file__).parent / "alert_config.json"]
    for p in candidates:
        if p.exists():
            try:
                cfg = json.loads(p.read_text())
                merged = dict(DEFAULT_THRESHOLDS)
                merged.update(cfg.get("thresholds", {}))
                return merged
            except Exception as e:
                print(f"WARN: failed to read {p}: {e}", file=sys.stderr)
    return dict(DEFAULT_THRESHOLDS)

THRESHOLDS = load_thresholds()
# ----- /Threshold loading -----



# ---------------------------------------------------------------------------
# Tunable thresholds  — start strict, loosen after a week of real data
# ---------------------------------------------------------------------------

@dataclass
class Rule:
    id: str
    severity: str              # 'critical' | 'warning'
    metric: str
    description: str
    min_sample: int            # below this, skip — too noisy
    evaluate: Callable[[dict, dict], tuple[bool, float, float, str] | None]


def _safe_div(a, b):
    return (a / b) if b else 0.0


def _agg_for(metric_rows: list[dict]) -> dict:
    """Aggregate a slice of rows into the metric set we evaluate against.

    Note: delivery_rate, open_rate, click_rate are computed only over channels
    that have a meaningful 'delivered' concept (email + push). Webhook and SMS
    are excluded from the rate denominators because they don't report
    'delivered' as a concept — webhooks have errors, SMS has rejected/failed,
    and including them in the denominator would deflate every rate.
    """
    delivery_channels = lambda r: r.get("channel") == "email" or (
        r.get("channel") or "").endswith("_push")

    sent_for_delivery = sum((r.get("sent") or 0) for r in metric_rows
                            if delivery_channels(r))
    sent_total = sum((r.get("sent") or 0) for r in metric_rows)
    delivered_email = sum((r.get("delivered") or 0) for r in metric_rows
                          if r.get("channel") == "email")
    sent_push = sum((r.get("sent") or 0) for r in metric_rows
                    if (r.get("channel") or "").endswith("_push"))
    bounces_push = sum((r.get("bounces") or 0) for r in metric_rows
                       if (r.get("channel") or "").endswith("_push"))
    delivered = delivered_email + max(0, sent_push - bounces_push)
    bounces = sum((r.get("bounces") or 0) for r in metric_rows
                  if delivery_channels(r))
    errors = sum((r.get("errors") or 0) for r in metric_rows
                 if r.get("channel") == "webhook")
    sent_webhook = sum((r.get("sent") or 0) for r in metric_rows
                       if r.get("channel") == "webhook")
    unsubs = sum((r.get("unsubscribes") or 0) for r in metric_rows)
    opens_email = sum((r.get("unique_opens") or 0) for r in metric_rows
                      if r.get("channel") == "email")
    opens_push = sum((r.get("direct_opens") or 0) for r in metric_rows
                     if (r.get("channel") or "").endswith("_push"))
    opens = opens_email + opens_push
    clicks_email = sum((r.get("unique_clicks") or 0) for r in metric_rows
                       if r.get("channel") == "email")
    clicks_push = sum((r.get("body_clicks") or 0) for r in metric_rows
                      if (r.get("channel") or "").endswith("_push"))
    clicks = clicks_email + clicks_push
    return {
        "sent": sent_total,                      # for volume_collapse rule
        "sent_for_delivery": sent_for_delivery,  # for delivery / bounce rules
        "sent_webhook": sent_webhook,            # for webhook errors rule
        "delivered": delivered, "bounces": bounces,
        "errors": errors, "unsubscribes": unsubs,
        "delivery_rate": _safe_div(delivered, sent_for_delivery),
        "open_rate": _safe_div(opens, delivered),
        "click_rate": _safe_div(clicks, delivered),
        "unsub_rate": _safe_div(unsubs, delivered),
        "bounce_rate": _safe_div(bounces, sent_for_delivery),
        "error_rate": _safe_div(errors, sent_webhook),
    }


def rule_open_rate_drop(cur, prev):
    if cur["sent"] < THRESHOLDS['all_min_sample']: return None
    delta = cur["open_rate"] - prev["open_rate"]   # in fraction
    if delta * 100 <= THRESHOLDS['email_open_drop_pp']:  # 5 percentage points
        return True, cur["open_rate"], prev["open_rate"], "critical"
    if delta * 100 <= THRESHOLDS['email_open_drop_pp'] / 2:
        return True, cur["open_rate"], prev["open_rate"], "warning"
    return None


def rule_click_rate_drop(cur, prev):
    if cur["sent"] < THRESHOLDS['all_min_sample']: return None
    delta = cur["click_rate"] - prev["click_rate"]
    if delta * 100 <= THRESHOLDS['email_click_drop_pp']:
        return True, cur["click_rate"], prev["click_rate"], "critical"
    if delta * 100 <= THRESHOLDS['email_click_drop_pp'] / 4:
        return True, cur["click_rate"], prev["click_rate"], "warning"
    return None


def rule_delivery_rate_cliff(cur, prev):
    if cur["sent_for_delivery"] < 500: return None
    if cur["delivery_rate"] < THRESHOLDS['email_delivery_min'] - 0.05:
        return True, cur["delivery_rate"], prev["delivery_rate"], "critical"
    if cur["delivery_rate"] < THRESHOLDS['email_delivery_min'] and prev["delivery_rate"] >= 0.97:
        return True, cur["delivery_rate"], prev["delivery_rate"], "warning"
    return None


def rule_unsub_spike(cur, prev):
    if cur["delivered"] < 1000: return None
    if cur["unsub_rate"] > THRESHOLDS['email_unsub_max'] and cur["unsub_rate"] > prev["unsub_rate"] * 2:
        return True, cur["unsub_rate"], prev["unsub_rate"], "critical"
    if cur["unsub_rate"] > THRESHOLDS['email_unsub_max'] * 0.6 and cur["unsub_rate"] > prev["unsub_rate"] * 1.5:
        return True, cur["unsub_rate"], prev["unsub_rate"], "warning"
    return None


def rule_bounce_spike(cur, prev):
    if cur["sent_for_delivery"] < 500: return None
    if cur["bounce_rate"] > 0.05:
        return True, cur["bounce_rate"], prev["bounce_rate"], "critical"
    if cur["bounce_rate"] > 0.02 and cur["bounce_rate"] > prev["bounce_rate"] * 2:
        return True, cur["bounce_rate"], prev["bounce_rate"], "warning"
    return None


def rule_webhook_errors(cur, prev):
    if cur["sent_webhook"] < 200: return None
    if cur["error_rate"] > 0.1:
        return True, cur["error_rate"], prev["error_rate"], "critical"
    if cur["error_rate"] > 0.05:
        return True, cur["error_rate"], prev["error_rate"], "warning"
    return None


def rule_volume_collapse(cur, prev):
    """Catches 'the trigger broke' — current sends way below prior baseline."""
    if prev["sent"] < THRESHOLDS['all_min_sample'] * 0.7:  # need enough baseline volume to flag
        return None
    ratio = cur["sent"] / max(prev["sent"], 1)
    if ratio < THRESHOLDS['all_volume_collapse_pct']:
        return True, float(cur["sent"]), float(prev["sent"]), "critical"
    if ratio < THRESHOLDS['all_volume_collapse_pct'] + 0.2:
        return True, float(cur["sent"]), float(prev["sent"]), "warning"
    return None



def rule_spam_spike(cur, prev):
    if cur["delivered"] < THRESHOLDS['all_min_sample']: return None
    spam_rate = cur.get("reported_spam", 0) / max(cur.get("delivered", 1), 1) if cur.get("delivered") else 0
    prev_spam = prev.get("reported_spam", 0) / max(prev.get("delivered", 1), 1) if prev.get("delivered") else 0
    if spam_rate > THRESHOLDS['email_spam_max']:
        return True, spam_rate, prev_spam, "critical"
    if spam_rate > THRESHOLDS['email_spam_max'] * 0.5 and spam_rate > prev_spam * 2:
        return True, spam_rate, prev_spam, "warning"
    return None

RULES = [
    ("open_rate_drop",     "open_rate",     "Open rate dropped sharply",   rule_open_rate_drop),
    ("click_rate_drop",    "click_rate",    "Click rate dropped sharply",  rule_click_rate_drop),
    ("delivery_cliff",     "delivery_rate", "Delivery rate fell",          rule_delivery_rate_cliff),
    ("unsub_spike",        "unsub_rate",    "Unsubscribe rate spiked",     rule_unsub_spike),
    ("bounce_spike",       "bounce_rate",   "Bounce rate elevated",        rule_bounce_spike),
    ("webhook_errors",     "error_rate",    "Webhook error rate elevated", rule_webhook_errors),
    ("volume_collapse",    "sent",          "Send volume collapsed",       rule_volume_collapse),
    ("spam_spike",         "spam_rate",     "Spam complaint rate elevated", rule_spam_spike),
]


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------

def load_rows_from_sqlite(db_path: Path) -> tuple[list[dict], list[dict]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM message_metrics_daily WHERE channel != '__total__' AND channel != 'none'"
    ).fetchall()]
    flows = [dict(r) for r in conn.execute(
        "SELECT flow_id, flow_type, name FROM flows WHERE COALESCE(archived, 0)=0"
    ).fetchall()]
    conn.close()
    return rows, flows


def load_rows_from_json(json_path: Path) -> tuple[list[dict], list[dict]]:
    payload = json.loads(json_path.read_text())
    rows = [r for r in payload.get("rows", [])
            if r.get("channel") not in ("__total__", "none")]
    flows = [{"flow_id": f["flow_id"], "flow_type": f["flow_type"], "name": f["name"]}
             for f in payload.get("flows", [])]
    return rows, flows


# ---------------------------------------------------------------------------
# Alert evaluation
# ---------------------------------------------------------------------------

def evaluate(rows: list[dict], flows: list[dict],
             window_days: int = 7) -> list[dict]:
    if not rows:
        return []
    end = max(r["date"] for r in rows)
    end_dt = datetime.fromisoformat(end)
    cur_cutoff = (end_dt - timedelta(days=window_days)).date().isoformat()
    prev_cutoff = (end_dt - timedelta(days=window_days * 2)).date().isoformat()

    by_flow: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        by_flow.setdefault((r["flow_id"], r["flow_type"]), []).append(r)

    flow_name = {(f["flow_id"], f["flow_type"]): f["name"] for f in flows}

    detected_at = datetime.now(timezone.utc).isoformat()
    out = []

    for (flow_id, flow_type), flow_rows in by_flow.items():
        cur = [r for r in flow_rows if r["date"] > cur_cutoff]
        prev = [r for r in flow_rows if prev_cutoff < r["date"] <= cur_cutoff]

        # Flow-level
        cur_agg = _agg_for(cur)
        prev_agg = _agg_for(prev)
        for rule_id, metric, descr, fn in RULES:
            res = fn(cur_agg, prev_agg)
            if not res: continue
            _, cv, bv, sev = res
            out.append({
                "detected_at": detected_at,
                "flow_id": flow_id, "flow_type": flow_type,
                "flow_name": flow_name.get((flow_id, flow_type), flow_id),
                "step_id": "__flow__", "step_name": "(flow level)",
                "channel": "__all__",
                "rule": rule_id, "severity": sev, "metric": metric,
                "current_value": cv, "baseline_value": bv,
                "sample_size": cur_agg["sent"],
                "message": _format_message(descr, metric, cv, bv, cur_agg["sent"], window_days),
            })

        # Step+channel-level for the same rules
        by_step_ch: dict[tuple[str, str, str], list[dict]] = {}
        for r in flow_rows:
            by_step_ch.setdefault(
                (r["step_id"], r.get("step_name", ""), r["channel"]), []
            ).append(r)
        for (step_id, step_name, channel), srows in by_step_ch.items():
            sc_cur = [r for r in srows if r["date"] > cur_cutoff]
            sc_prev = [r for r in srows if prev_cutoff < r["date"] <= cur_cutoff]
            sc_cur_agg = _agg_for(sc_cur)
            sc_prev_agg = _agg_for(sc_prev)
            for rule_id, metric, descr, fn in RULES:
                res = fn(sc_cur_agg, sc_prev_agg)
                if not res: continue
                _, cv, bv, sev = res
                out.append({
                    "detected_at": detected_at,
                    "flow_id": flow_id, "flow_type": flow_type,
                    "flow_name": flow_name.get((flow_id, flow_type), flow_id),
                    "step_id": step_id, "step_name": step_name,
                    "channel": channel,
                    "rule": rule_id, "severity": sev, "metric": metric,
                    "current_value": cv, "baseline_value": bv,
                    "sample_size": sc_cur_agg["sent"],
                    "message": _format_message(descr, metric, cv, bv,
                                               sc_cur_agg["sent"], window_days),
                })

    # de-dupe: if a flow-level alert and a step-level alert fire on the same
    # rule+metric, keep both; the dashboard shows them grouped
    return out


def _format_message(descr, metric, cur, base, sample, window):
    if metric in ("open_rate", "click_rate", "delivery_rate", "unsub_rate",
                  "bounce_rate", "error_rate"):
        cur_pct = f"{cur*100:.1f}%"
        base_pct = f"{base*100:.1f}%"
        delta_pp = (cur - base) * 100
        return (f"{descr}: {cur_pct} (vs {base_pct} prior {window}d, "
                f"{delta_pp:+.1f}pp); sample {sample:,} sends.")
    if metric == "sent":
        ratio = cur / max(base, 1)
        return (f"{descr}: {int(cur):,} sends vs {int(base):,} prior {window}d "
                f"({ratio*100:.0f}% of baseline).")
    return f"{descr}: cur={cur}, baseline={base}, sample={sample}"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def write_to_sqlite(db_path: Path, alerts: list[dict]) -> None:
    conn = sqlite3.connect(db_path)
    for a in alerts:
        conn.execute("""
            INSERT OR REPLACE INTO alerts
            (detected_at, flow_id, flow_type, flow_name,
             step_id, step_name, channel,
             rule, severity, metric, current_value, baseline_value,
             sample_size, message)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (a["detected_at"], a["flow_id"], a["flow_type"], a["flow_name"],
             a["step_id"], a["step_name"], a["channel"],
             a["rule"], a["severity"], a["metric"],
             a["current_value"], a["baseline_value"],
             a["sample_size"], a["message"]))
    conn.commit()
    conn.close()


def merge_into_json(json_path: Path, alerts: list[dict]) -> None:
    payload = json.loads(json_path.read_text())
    payload["alerts"] = alerts
    json_path.write_text(json.dumps(payload, indent=2))


# ---------------------------------------------------------------------------
# Slack digest
# ---------------------------------------------------------------------------

def post_to_slack(alerts: list[dict], webhook: str) -> None:
    if not alerts:
        msg = ":white_check_mark: *Braze daily digest* — all flows healthy. No alerts."
    else:
        flow_alerts = [a for a in alerts if a["step_id"] == "__flow__"]
        crit = [a for a in flow_alerts if a["severity"] == "critical"]
        warn = [a for a in flow_alerts if a["severity"] == "warning"]

        lines = [f":rotating_light: *Braze daily digest* — "
                 f"{len(crit)} critical, {len(warn)} warning"]
        if crit:
            lines.append("\n*:red_circle: Critical*")
            for a in crit[:10]:
                lines.append(f"• *{a['flow_name']}* — {a['message']}")
        if warn:
            lines.append("\n*:large_yellow_circle: Warning*")
            for a in warn[:10]:
                lines.append(f"• *{a['flow_name']}* — {a['message']}")
        if len(flow_alerts) > 20:
            lines.append(f"\n_…and {len(flow_alerts) - 20} more. Open the dashboard for the full list._")

        msg = "\n".join(lines)

    import requests
    resp = requests.post(webhook, json={"text": msg}, timeout=10)
    if not resp.ok:
        print(f"Slack post failed: {resp.status_code} {resp.text}", file=sys.stderr)
    else:
        print(f"Slack digest posted ({len(alerts)} alert(s))", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--json", help="Path to dashboard_data.json (sample-data path)", default=None)
    p.add_argument("--rewrite", action="store_true",
                   help="Rewrite alerts in the JSON instead of just appending")
    p.add_argument("--window", type=int, default=7, help="Window in days (default 7)")
    p.add_argument("--slack", action="store_true", help="Also post to Slack")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()

    out_dir = Path(os.environ.get("BRAZE_OUT_DIR", "./out")).resolve()
    db_path = out_dir / "braze_metrics.db"
    json_path = Path(args.json) if args.json else (out_dir / "dashboard_data.json")

    if args.json:
        rows, flows = load_rows_from_json(json_path)
    elif db_path.exists():
        rows, flows = load_rows_from_sqlite(db_path)
    else:
        # fall back to JSON if db missing (e.g. running against sample data only)
        if not json_path.exists():
            print(f"ERROR: neither {db_path} nor {json_path} exist. "
                  f"Run braze_extract.py or generate_sample_data.py first.", file=sys.stderr)
            sys.exit(1)
        rows, flows = load_rows_from_json(json_path)

    alerts = evaluate(rows, flows, window_days=args.window)

    if not args.quiet:
        crit = sum(1 for a in alerts if a["severity"] == "critical")
        warn = sum(1 for a in alerts if a["severity"] == "warning")
        print(f"Detected {len(alerts)} alerts ({crit} critical, {warn} warning) "
              f"across {len(set((a['flow_id'], a['flow_type']) for a in alerts))} flow(s)",
              file=sys.stderr)

    if db_path.exists():
        write_to_sqlite(db_path, alerts)

    if json_path.exists():
        merge_into_json(json_path, alerts)
        if not args.quiet:
            print(f"Wrote {len(alerts)} alerts to {json_path}", file=sys.stderr)

    if args.slack:
        webhook = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
        if not webhook:
            print("ERROR: SLACK_WEBHOOK_URL env var required for --slack", file=sys.stderr)
            sys.exit(1)
        post_to_slack(alerts, webhook)


if __name__ == "__main__":
    main()
