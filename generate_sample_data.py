#!/usr/bin/env python3
"""
generate_sample_data.py
=======================
Produces a dashboard_data.json with realistic synthetic numbers — Canvases,
Campaigns, and a few sample alerts — so you can preview the dashboard before
pointing it at real Braze.

Usage:
  python generate_sample_data.py > dashboard_data.json
"""

import json
import random
import sys
from datetime import datetime, timedelta, timezone

random.seed(42)

DAYS = 30


def daterange(days: int):
    end = datetime.now(timezone.utc).date()
    for i in range(days - 1, -1, -1):
        yield (end - timedelta(days=i)).isoformat()


CANVASES = [
    {
        "flow_id": "c0000001-onboarding", "flow_type": "canvas",
        "name": "Onboarding — Day 0 to Day 7",
        "channels": ["email", "ios_push", "android_push"],
        "steps": [
            {"step_id": "s001", "step_name": "Welcome Email", "step_type": "message", "channels": ["email"]},
            {"step_id": "s002", "step_name": "Day 1 Push",    "step_type": "message", "channels": ["ios_push", "android_push"]},
            {"step_id": "s003", "step_name": "Day 3 Reminder","step_type": "message", "channels": ["email"]},
            {"step_id": "s004", "step_name": "Day 7 Activation Email", "step_type": "message", "channels": ["email"]},
        ],
        "scale": 5000, "open_rate": 0.42, "click_rate": 0.06,
    },
    {
        "flow_id": "c0000002-winback", "flow_type": "canvas",
        "name": "Winback — Lapsed 30d",
        "channels": ["email", "webhook"],
        "steps": [
            {"step_id": "s101", "step_name": "Email — We Miss You",  "step_type": "message", "channels": ["email"]},
            {"step_id": "s102", "step_name": "SMS — Last Chance",    "step_type": "message", "channels": ["webhook"]},
            {"step_id": "s103", "step_name": "Email — Final Offer",  "step_type": "message", "channels": ["email"]},
        ],
        "scale": 1800, "open_rate": 0.28, "click_rate": 0.035,
    },
    {
        "flow_id": "c0000003-promo", "flow_type": "canvas",
        "name": "Weekly Promo Blast",
        "channels": ["email", "android_push", "ios_push"],
        "steps": [
            {"step_id": "s201", "step_name": "Promo Email", "step_type": "message", "channels": ["email"]},
            {"step_id": "s202", "step_name": "Promo Push",  "step_type": "message", "channels": ["ios_push", "android_push"]},
        ],
        "scale": 12000, "open_rate": 0.38, "click_rate": 0.045,
        # plant a regression in the last 5 days for the alert engine to find
        "regression_days": 5, "regression_open_factor": 0.55,
    },
    {
        "flow_id": "c0000004-receipt", "flow_type": "canvas",
        "name": "Receipt + Survey",
        "channels": ["email"],
        "steps": [
            {"step_id": "s301", "step_name": "Receipt Email",      "step_type": "message", "channels": ["email"]},
            {"step_id": "s302", "step_name": "Day 2 Survey Email", "step_type": "message", "channels": ["email"]},
        ],
        "scale": 3200, "open_rate": 0.71, "click_rate": 0.18,
    },
    {
        "flow_id": "c0000005-reengagement", "flow_type": "canvas",
        "name": "Re-engagement Push Series",
        "channels": ["ios_push", "android_push"],
        "steps": [
            {"step_id": "s401", "step_name": "Push — 7d silent",  "step_type": "message", "channels": ["ios_push", "android_push"]},
            {"step_id": "s402", "step_name": "Push — 14d silent", "step_type": "message", "channels": ["ios_push", "android_push"]},
        ],
        "scale": 7400, "open_rate": 0.12, "click_rate": 0.02,
    },
]

CAMPAIGNS = [
    {
        "flow_id": "cmp00001-blackfriday", "flow_type": "campaign",
        "name": "Black Friday Email Blast",
        "channels": ["email"],
        "steps": [{"step_id": "cmp00001-blackfriday::msg", "step_name": "Campaign Message", "step_type": "message", "channels": ["email"]}],
        "scale": 28000, "open_rate": 0.46, "click_rate": 0.092,
    },
    {
        "flow_id": "cmp00002-newsletter", "flow_type": "campaign",
        "name": "Weekly Newsletter",
        "channels": ["email"],
        "steps": [{"step_id": "cmp00002-newsletter::msg", "step_name": "Campaign Message", "step_type": "message", "channels": ["email"]}],
        "scale": 18500, "open_rate": 0.34, "click_rate": 0.038,
    },
    {
        "flow_id": "cmp00003-flashpush", "flow_type": "campaign",
        "name": "Flash Sale Push",
        "channels": ["ios_push", "android_push"],
        "steps": [{"step_id": "cmp00003-flashpush::msg", "step_name": "Campaign Message", "step_type": "message", "channels": ["ios_push", "android_push"]}],
        "scale": 9200, "open_rate": 0.18, "click_rate": 0.058,
        # plant a delivery-rate cliff
        "regression_days": 2, "delivery_factor": 0.78,
    },
    {
        "flow_id": "cmp00004-survey", "flow_type": "campaign",
        "name": "Customer Survey Invite",
        "channels": ["email"],
        "steps": [{"step_id": "cmp00004-survey::msg", "step_name": "Campaign Message", "step_type": "message", "channels": ["email"]}],
        "scale": 4400, "open_rate": 0.51, "click_rate": 0.21,
    },
]


def synth_email(date_idx, total_days, base_sent, open_rate, click_rate,
                regression_factor=1.0, delivery_factor=1.0):
    dow = date_idx % 7
    cycle = 0.85 + 0.3 * (1 if dow in (1, 2, 3) else 0.6)
    sent = max(0, int(base_sent * cycle * random.uniform(0.85, 1.15)))
    delivered = int(sent * random.uniform(0.96, 0.99) * delivery_factor)
    bounces = sent - delivered
    unique_opens = int(delivered * open_rate * regression_factor * random.uniform(0.9, 1.1))
    opens = int(unique_opens * random.uniform(1.4, 1.8))
    unique_clicks = int(delivered * click_rate * regression_factor * random.uniform(0.9, 1.1))
    clicks = int(unique_clicks * random.uniform(1.1, 1.4))
    return {
        "sent": sent, "delivered": delivered, "bounces": bounces,
        "opens": opens, "unique_opens": unique_opens,
        "clicks": clicks, "unique_clicks": unique_clicks,
        "unsubscribes": int(delivered * 0.0012 * random.uniform(0.5, 1.5)),
        "reported_spam": int(delivered * 0.0003 * random.uniform(0.0, 2.0)),
        "machine_open": int(unique_opens * 0.18),
        "machine_amp_open": 0,
    }


def synth_push(date_idx, total_days, base_sent, open_rate,
               regression_factor=1.0, delivery_factor=1.0):
    cycle = 0.9 + 0.2 * random.uniform(0, 1)
    sent = max(0, int(base_sent * cycle * random.uniform(0.85, 1.15)))
    # delivery_factor < 1.0 means a real delivery cliff: many more bounces.
    # e.g. 0.78 → ~22% bounce rate, well below the 90% delivery threshold.
    base_bounce_rate = random.uniform(0.005, 0.02)
    if delivery_factor < 1.0:
        # interpret delivery_factor as the *target* delivery rate
        bounce_rate = max(base_bounce_rate, 1.0 - delivery_factor)
    else:
        bounce_rate = base_bounce_rate
    bounces = int(sent * bounce_rate)
    direct_opens = int(sent * open_rate * regression_factor * random.uniform(0.85, 1.15))
    total_opens = int(direct_opens * random.uniform(1.1, 1.3))
    body_clicks = int(direct_opens * random.uniform(0.4, 0.7))
    return {
        "sent": sent, "bounces": bounces,
        "direct_opens": direct_opens, "total_opens_push": total_opens,
        "body_clicks": body_clicks,
    }


def synth_webhook(base_sent):
    sent = max(0, int(base_sent * random.uniform(0.85, 1.15)))
    return {"sent": sent, "errors": int(sent * random.uniform(0.005, 0.03))}


def empty_metric_row():
    return {
        "sent": 0, "delivered": 0, "bounces": 0, "errors": 0,
        "revenue": 0.0, "conversions": 0, "unique_recipients": 0, "entries": 0,
        "opens": 0, "unique_opens": 0, "clicks": 0, "unique_clicks": 0,
        "unsubscribes": 0, "reported_spam": 0, "machine_open": 0, "machine_amp_open": 0,
        "direct_opens": 0, "total_opens_push": 0, "body_clicks": 0,
        "sent_to_carrier": 0, "sms_delivered": 0, "sms_rejected": 0,
        "sms_delivery_failed": 0, "sms_clicks": 0, "sms_opt_out": 0, "sms_help": 0,
    }


def build():
    rows = []
    all_flows = CANVASES + CAMPAIGNS
    dates = list(daterange(DAYS))

    for flow in all_flows:
        for di, date in enumerate(dates):
            # planted-regression handling
            days_from_end = (len(dates) - 1) - di
            reg_factor = 1.0
            del_factor = 1.0
            if "regression_days" in flow and days_from_end < flow["regression_days"]:
                reg_factor = flow.get("regression_open_factor", 1.0)
                del_factor = flow.get("delivery_factor", 1.0)

            entries = max(0, int(flow["scale"] * random.uniform(0.85, 1.15)))
            tr = empty_metric_row()
            tr.update({
                "date": date, "flow_id": flow["flow_id"], "flow_type": flow["flow_type"],
                "flow_name": flow["name"], "step_id": "__total__", "step_name": (
                    "Canvas Total" if flow["flow_type"] == "canvas" else "Campaign Total"),
                "channel": "__total__", "variant_id": None,
                "entries": entries if flow["flow_type"] == "canvas" else 0,
                "revenue": round(entries * random.uniform(0.05, 0.4), 2),
                "conversions": int(entries * random.uniform(0.02, 0.08)),
                "unique_recipients": entries,
            })
            rows.append(tr)

            for step in flow["steps"]:
                base_per_ch = int(flow["scale"] / max(len(step["channels"]), 1)
                                  * random.uniform(0.6, 0.95))
                for ch in step["channels"]:
                    r = empty_metric_row()
                    r.update({
                        "date": date, "flow_id": flow["flow_id"], "flow_type": flow["flow_type"],
                        "flow_name": flow["name"], "step_id": step["step_id"],
                        "step_name": step["step_name"], "channel": ch,
                        "variant_id": f"v-{step['step_id']}-{ch}",
                        "unique_recipients": base_per_ch,
                    })
                    if ch == "email":
                        r.update(synth_email(di, DAYS, base_per_ch,
                                             flow["open_rate"], flow["click_rate"],
                                             reg_factor, del_factor))
                    elif ch in ("ios_push", "android_push", "web_push"):
                        r.update(synth_push(di, DAYS, base_per_ch,
                                            flow["open_rate"] * 0.55,
                                            reg_factor, del_factor))
                    elif ch == "webhook":
                        r.update(synth_webhook(base_per_ch))
                    rows.append(r)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "endpoint": "https://rest.iad-05.braze.com (SAMPLE DATA — synthetic)",
        "days_pulled": DAYS,
        "flows": [
            {k: v for k, v in f.items() if k not in (
                "scale", "open_rate", "click_rate",
                "regression_days", "regression_open_factor", "delivery_factor")}
            for f in all_flows
        ],
        "rows": rows,
        "alerts": [],  # populated by alerts.py at runtime
    }


if __name__ == "__main__":
    json.dump(build(), sys.stdout, indent=2)
