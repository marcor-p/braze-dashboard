# Braze KPI Dashboard

Daily-refreshed dashboard for **Canvases + Campaigns**, with an alert engine
that flags performance regressions (open-rate drops, delivery cliffs,
unsubscribe spikes, etc.). Single-folder local install at
**http://localhost:8000**.

```
braze_extract.py        # extracts Canvases + Campaigns from Braze REST API
alerts.py               # runs threshold rules → SQLite + JSON + Slack
generate_sample_data.py # synthetic data + planted regressions for preview
serve.py                # tiny Flask server (localhost + Refresh button)
run.sh / run.bat        # one-command launcher
index.html              # dashboard UI (Overview / Detail / Alerts tabs)
dashboard_data.json     # what the UI loads (extractor or sample writes this)
out/braze_metrics.db    # normalized SQLite — query however you like
.github/workflows/braze_daily.yml  # daily extract + alerts + Slack
```

---

## 1. Quick start (60s, no Braze key)

### macOS / Linux
```bash
chmod +x run.sh
./run.sh --sample
```

### Windows
```cmd
run.bat --sample
```

Open **http://localhost:8000**. You'll see 5 fake Canvases, 4 fake Campaigns,
and a few planted alerts (one Canvas with an open-rate cliff, one Campaign
with a delivery drop) so the Alerts tab isn't empty.

---

## 2. Connect to real Braze

### 2a. Get your endpoint and API key

1. **Endpoint** — find your cluster URL at
   https://www.braze.com/docs/api/basics/#endpoints
2. **API key** — Braze: *Settings → API Keys*. Required scopes:
   - `canvas.list`, `canvas.details`, `canvas.data_series`
   - `campaigns.list`, `campaigns.details`, `campaigns.data_series`

### 2b. Run with live data

```bash
export BRAZE_API_KEY="..."
export BRAZE_REST_ENDPOINT="https://rest.iad-05.braze.com"
./run.sh --extract        # pulls fresh Canvas + Campaign data, runs alerts, then serves
```

Or use the **Refresh data** button on the dashboard to re-run extraction
on demand without restarting.

---

## 3. Modes

`run.sh` flags:

| Flag         | Behavior |
|--------------|----------|
| (none)       | Serve current `dashboard_data.json` (sample-generates if missing) |
| `--sample`   | Regenerate synthetic data + alerts before serving |
| `--extract`  | Live extract from Braze + alerts before serving |
| `--slack`    | Also post Slack digest after alerts run |
| `--static`   | Plain `http.server`; no Flask; no refresh button |

You can combine: `./run.sh --extract --slack`.

---

## 4. The three views

The dashboard has three tabs in the top-left:

### All flows (Overview)
One row per Canvas / Campaign with macro KPIs over the selected window:
Sent / Delivery % / Open % / Δ Open % / Click % / Conversions / Unsubs / Health.
Sortable on every column. Filter by Type (Canvases / Campaigns / Both).
Click a row → drills into Detail with that flow pre-selected.

### Detail
Original per-flow view: 8 KPI cards with deltas, daily-evolution line chart,
channel-mix donut, per-step KPI table. Filter by Type / Flow / Channel /
Step / Window.

### Alerts
Every regression the alert engine detected, sorted by severity, with the
flow name, what fired, the numbers, and the rule that triggered it.
Click an alert → jumps to that flow in Detail view.

---

## 5. Alert rules

Defined in `alerts.py`. Each rule has a min sample size to suppress noise
on tiny audiences:

| Rule | Critical | Warning | Min sample |
|---|---|---|---|
| Open rate drop | −5pp vs prior 7d | −2pp | ≥1000 sends |
| Click rate drop | −2pp | −0.5pp | ≥1000 sends |
| Delivery rate cliff | <90% | <95% (was ≥97%) | ≥500 sends |
| Unsub spike | >0.5% AND 2× baseline | >0.3% AND 1.5× | ≥1000 delivered |
| Bounce spike | >5% | >2% AND 2× | ≥500 sends |
| Webhook errors | >10% | >5% | ≥200 sends |
| Volume collapse | <50% of baseline | <70% | baseline ≥700 |

Rules run against **flow-level totals** AND **each (step × channel) cell**,
so you'll see both "the whole Canvas regressed" and "the iOS push step in
the Promo Canvas regressed" alerts when applicable.

To tune: edit the rule functions at the top of `alerts.py`. After a week
of real data you'll know whether your defaults need to be stricter or
looser for your workspace's natural variance.

---

## 6. Slack digest

`alerts.py --slack` posts a daily digest to a Slack incoming webhook:

```
🚨 Braze daily digest — 2 critical, 3 warning

🔴 Critical
• Weekly Promo Blast — Open rate dropped sharply: 21.0% (vs 38.0% prior 7d, -17.0pp); sample 84,930 sends.
• Flash Sale Push — Delivery rate fell: 78.0% (vs 99.0% prior 7d, -21.0pp); sample 18,200 sends.

🟡 Warning
• ...
```

Setup:
1. Create a Slack incoming webhook in your workspace (Apps → Incoming Webhooks).
2. `export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."`
3. `./run.sh --slack` (or `python alerts.py --slack` standalone).

The digest only includes **flow-level** alerts to avoid noise from
per-step regressions — if 5 step+channel combinations all fire on the
same flow, they're consolidated under the flow name in Slack but all
visible in the dashboard's Alerts tab.

---

## 7. Schedule it

The included GitHub Actions workflow runs daily at 06:30 UTC, extracting
+ running alerts + posting Slack + committing the refreshed JSON back to
the repo. Add three secrets to your repo:

- `BRAZE_API_KEY`
- `BRAZE_REST_ENDPOINT`
- `SLACK_WEBHOOK_URL` (optional)

Other working schedulers: cron, Devin scheduled session, n8n flow.
The alerts.py script reads from the SQLite that braze_extract.py writes,
so any orchestrator that runs them in order works.

---

## 8. SQL access

`out/braze_metrics.db` has four tables:

- `flows` — one row per Canvas/Campaign with metadata
- `flow_steps` — one row per step in each flow
- `message_metrics_daily` — fact table: one row per
  `(date, flow_id, flow_type, step_id, channel, variant_id)`
- `alerts` — historical alerts (each run appends; use `detected_at` to
  filter to the latest)

Example: 7-day rollup per flow:

```sql
SELECT
  flow_type, flow_name,
  SUM(sent) AS sent_7d,
  ROUND(1.0 * SUM(unique_opens) / NULLIF(SUM(delivered), 0), 4) AS open_rate_7d,
  ROUND(1.0 * SUM(unique_clicks) / NULLIF(SUM(delivered), 0), 4) AS click_rate_7d
FROM message_metrics_daily
WHERE date >= date('now', '-7 days')
  AND channel = 'email'
GROUP BY 1, 2
ORDER BY sent_7d DESC;
```

---

## 9. Known limits

- **Canvas data_series caps at 14 days/call**; campaigns at 100. Extractor
  handles pagination for Canvases automatically.
- **Today's numbers are incomplete** for ~24h. Extractor re-pulls the full
  window every run; idempotent upserts handle overlap.
- **No auto-disable of flows.** By design: thresholds fire false positives
  too often to safely automate flow pause/disable. Alerts notify; humans act.
- **Webhook delivery truth lives at the receiver** (e.g. Twilio). Braze
  only knows whether the HTTP call returned an error, not whether the SMS
  reached the recipient.
- **Push fields differ per platform**: web push lacks `total_opens`. The
  schema stores `total_opens_push`; web rows leave it 0.
- **Flask's dev server** is local-only by design (`127.0.0.1`). Don't
  expose to public networks without a real WSGI server + auth.

---

## 10. Troubleshooting

**`./run.sh: Permission denied`** → `chmod +x run.sh`

**Port 8000 in use** → `PORT=8080 ./run.sh`

**Refresh button missing** → `--static` mode or `file://` open. Use
`./run.sh` (default) instead.

**401/403 from Braze** → wrong endpoint cluster (iad-05 vs fra-01 vs
others) or API key missing scopes. Re-issue the key with `canvas.*` and
`campaigns.*` read scopes.

**Alerts tab empty after a real extract** → expected if your flows are all
healthy. Confirm with `python alerts.py` — it prints the count to stderr.

**Sample data shows no alerts** → the planted regressions only fire when
you run `python alerts.py --json dashboard_data.json --rewrite` after
generating the sample. `./run.sh --sample` does this for you.
