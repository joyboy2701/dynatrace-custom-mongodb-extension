# MongoDB Custom Query → Dynatrace Logs

Dynatrace **Extension 2.0** (`custom:mongodb-query`) that runs on an **Environment ActiveGate**, executes read-only aggregation pipelines against MongoDB, and sends structured logs to Grail.

**`log.source`** in Grail: `mongodb_custom_query`

Hand this README to a colleague for a **first end-to-end test run**.

---

## Test run checklist

Use this as your step-by-step guide. Check each box before moving on.

| Step | Action | Done? |
|------|--------|-------|
| 1 | MongoDB reachable from ActiveGate; read-only user exists | ☐ |
| 2 | Seed demo collection `dt_demo_orders` (Part 1) | ☐ |
| 3 | Python venv + `dt-sdk` installed (Part 2) | ☐ |
| 4 | Developer certs uploaded (`ca.pem` → vault + ActiveGate) | ☐ |
| 5 | Extension built and uploaded to tenant (Part 2) | ☐ |
| 6 | Monitoring configuration saved on correct ActiveGate group (Part 3) | ☐ |
| 7 | Grail shows `query.status = success` (Part 4) | ☐ |

**Time estimate:** ~30–45 minutes (first time, including cert setup).

---

## What you need

| Item | Notes |
|------|--------|
| Dynatrace tenant URL | e.g. `https://your-tenant.live.dynatrace.com` (not `apps.dynatrace.com`) |
| API access token | Scopes: `extensions.write` (required) + `extensions.read` (recommended). See [API token scopes](#api-token-scopes) |
| Environment ActiveGate | Extensions module enabled; network path to MongoDB |
| MongoDB | Host, port, database name, password for a **read-only** user |
| `mongosh` client | To seed demo data  |
| Python 3.10+ | On your laptop for building/uploading the extension |

Replace placeholders below with your values:

| Variable | Example |
|----------|---------|
| `DB_HOST` | `your-mongodb-host` |
| `DB_PORT` | `27017` |
| `DB_NAME` | `demo` |
| `DB_USER` | `dtuser` |

---

## Repository layout

```text
MongoDB-Query-Logs/
├── README.md                      ← this guide
├── requirements.txt               ← dt-sdk + pymongo for build
├── .env.example                     ← upload credentials (laptop only)
├── scripts/
│   ├── seed_demo_data.js         ← creates dt_demo_orders + sample rows
│   ├── seed_demo_data.sh          ← runs the setup via mongosh
│   ├── export_queries_json.sh     ← one-line JSON for Dynatrace UI
│   ├── build_custom_integration.sh
│   └── upload_extension.sh        ← upload signed zip via dt-sdk
└── custom_integration/
    ├── queries.json               ← 6 sample monitoring queries
    ├── extension/                 ← extension.yaml, activationSchema.json
    ├── mongodb_custom_query/     ← Python package path used by the extension runtime
    ├── build.sh
    └── dist/                      ← signed zip appears here after build
```

---

## Part 1 — Seed MongoDB demo data

The sample pipelines use the `dt_demo_orders` collection. Run the setup once with a user allowed to insert documents:

```bash
export DB_HOST=your-mongodb-host
export DB_PORT=27017
export DB_NAME=demo
export DB_USER=demo_owner
export DB_AUTH_SOURCE=admin
./scripts/seed_demo_data.sh
```

`mongosh` prompts for the password. Give the extension account the MongoDB `read` role on `demo`; use that account for monitoring. Seeding again inserts additional demo documents.

Verify network access from ActiveGate:

```bash
nc -zv your-mongodb-host 27017
```

## Part 2 — Build and upload the extension

All steps below run on a **developer laptop**, not on the ActiveGate.

### 2.1 Python environment

```bash
cd MongoDB-Query-Logs
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### API token scopes

`dt-sdk upload` uses the **Extensions 2.0 API** (`POST /api/v2/extensions`). Create an **API access token** with these scopes:

| Scope (UI label) | API name | Why |
|------------------|----------|-----|
| **Write extensions** | `extensions.write` | Required — uploads the zip |
| **Read extensions** | `extensions.read` | Recommended — verify/list versions |

In Dynatrace: **Settings → Integration → Dynatrace API → Generate token** (or **Access tokens → Generate new token**), search for and tick `extensions.write` and `extensions.read`, then generate.

> Notes:
> - The old `PluginUpload` ("Upload Extension") scope is for Extensions **1.0** and does **not** work here.
> - If you use a newer **platform token** instead, the equivalents are `extensions:definitions:write` and `extensions:definitions:read`.
> - Monitoring configuration is created in the UI, so `extensionConfigurations.*` scopes are not required for this guide.

### 2.2 Certificates (first time only)

```bash
dt-sdk gencerts
```

Then:

1. **Dynatrace → Credential Vault** → upload `~/.dynatrace/certificates/ca.pem` as **Public certificate**, scope **Extension validation**
2. **ActiveGate** → copy the same file to:
   `/var/lib/dynatrace/remotepluginmodule/agent/conf/certificates/root.pem`
3. Restart the Extensions module / ActiveGate if required by your environment

### 2.3 Build signed zip (Linux ActiveGate target)

```bash
chmod +x scripts/build_custom_integration.sh
./scripts/build_custom_integration.sh
```

The build script prints the generated MongoDB extension zip path.

Or manually:

```bash
cd custom_integration
./build.sh
```

### 2.4 Upload to Dynatrace

Set credentials once:

```bash
cp .env.example .env
# Edit .env: set DT_ENV_URL and DT_API_TOKEN (token from "API token scopes" above)
```

Then upload with the helper script (sources `.env`, finds the latest built zip):

```bash
chmod +x scripts/upload_extension.sh
./scripts/upload_extension.sh
```

Or do it manually:

```bash
set -a && source .env && set +a
export DT_API_URL="$DT_ENV_URL"
./scripts/upload_extension.sh
```

> If upload fails with **403 / insufficient scope**, the token is missing `extensions.write`.
> If it fails with a **URL/host error**, `DT_ENV_URL` must be the `live.dynatrace.com` environment URL, not `apps.dynatrace.com`.

---

## Part 3 — Monitoring configuration

1. **Hub → Extensions → MongoDB Custom Query** (`custom:mongodb-query`, version **0.1.40**)
2. **Add monitoring configuration**
   - Scope: **Environment**
   - ActiveGate group that can reach MongoDB

### 3.1 Endpoint settings

| Field | Value |
|--------|--------|
| Host | your MongoDB host |
| Port | `27017` |
| Database name | `demo` |
| User | `dtuser` |
| Password | DB password |
| Query timeout (seconds) | `10` |
| Max rows per query | `1000` (or leave empty) |
| Default result mode | `row_per_result` (or leave empty) |
| log.source value | `mongodb_custom_query` |
| Enable dedup | `true` |
| Respect frequency_minutes | `false` for first test (logs every cycle); `true` for production |

### 3.2 Queries — pick one method

**Option A — MongoDB queries list (UI)**

Click **Add MongoDB query** for each entry in `custom_integration/queries.json`.

| Field | Example |
|--------|---------|
| Query name | `orders_by_status` |
| Enabled | `true` |
| Run immediately | `false` (default); enable to request a run after settings change |
| Frequency (minutes) | `1`, `5`, `10`, `15`, `30`, or `60` only |
| Collection | `dt_demo_orders` |
| Aggregation pipeline (JSON) | JSON array of stages from `queries.json`; `[]` returns documents |
| Dedup enabled | `true` |
| Dedup mode | `send_on_change` |
| Dedup key fields | e.g. `status` or `id` (comma-separated) |

**Option B — Advanced: queries JSON (faster)**

Leave the MongoDB queries list empty. Generate one line:

```bash
./scripts/export_queries_json.sh
```

Paste the output into **Advanced: queries JSON**.

### Run immediately (0.1.40)

Each query has a **Run immediately** toggle, OFF by default. Advanced JSON queries
can use `"run_immediately": true`. When updated settings reach ActiveGate, every
Enabled query with this toggle ON runs once on the **next monitoring cycle**, even
if its frequency is not due. The toggle remains ON in the UI. Any settings change
in the monitoring configuration triggers all ON queries across all its endpoints:
changing frequency to 30 minutes, adding a fifth query, or editing another endpoint.
Leaving settings unchanged, saving identical settings, or restarting on the same
ActiveGate with the same state does not repeat a handled request. Multiple updates
before a cycle collapse to the latest settings. An OFF state must reach a monitoring
cycle before turning ON again can request a repeat of earlier identical settings.

Manual execution does not update regular last-run timestamps, even when a scheduled
run is also due; the regular scheduler resumes on subsequent cycles. Enabled checks,
read-only restrictions, credentials, timeouts, result limits, deduplication, log
formats and error reporting still apply. Deduplication can therefore suppress rows
from a successful manual execution.

The installed SDK exposes no supported configuration revision/update identifier.
Detection hashes stable JSON serialization of the SDK's actual `ActivationConfig.config`
(all endpoints), plus its public version, enabled, description and feature-set values;
it does not serialize the empty inherited dict. Handled state uses a separate
`manual_request_state` table in the existing local SQLite database, without changing
scheduler or dedup tables. Only SHA-256 fingerprints are stored for manual requests,
never credentials or complete settings. Identity includes the SDK monitoring
configuration ID, endpoint position/host/port/database, and unique query name.
Renaming a query, moving/changing an endpoint's identity, deleting state, or switching
ActiveGates can make a request new. Local state may not follow execution to another
ActiveGate. The SDK must supply `monitoring_config_id`; missing identity or a state
write failure is reported as an endpoint setup error rather than risking replay.

Requests are **committed before connecting to MongoDB or executing queries**.
Connection/query failures do not retry the manual request every cycle. An interruption
after that commit may lose the requested execution (or remaining executions on that
endpoint); this is the consume-before-execution tradeoff. Change settings again to
request another run. Normal scheduled retries continue according to existing behavior.
Unobserved intermediate settings, including OFF, cannot be detected without a revision
stream. Credential-vault value changes also count if the resolved SDK settings change.

### 3.3 Sample queries reference

| Query name | Frequency | Dedup keys | What it checks |
|------------|-----------|------------|----------------|
| `connection_test` | 1 min | test_value | DB connectivity |
| `orders_by_status` | 1 min | status | Counts by order status |
| `orders_by_region` | 5 min | region | Counts by region |
| `recent_orders` | 1 min | id | Latest 10 orders |
| `pending_orders_summary` | 1 min | status | Pending order totals |
| `orders_last_hour` | 5 min | orders_last_hour | Orders in last hour |

Example pipeline for `orders_by_status` (collection `dt_demo_orders`):

```json
[
  {
    "$group": {
      "_id": "$status",
      "order_count": {
        "$sum": 1
      },
      "total_amount": {
        "$sum": "$amount"
      },
      "avg_amount": {
        "$avg": "$amount"
      }
    }
  },
  {
    "$project": {
      "_id": 0,
      "status": "$_id",
      "order_count": 1,
      "total_amount": 1,
      "avg_amount": {
        "$round": [
          "$avg_amount",
          2
        ]
      }
    }
  },
  {
    "$sort": {
      "status": 1
    }
  }
]
```

Dedup key fields: `status`

3. **Save** the monitoring configuration. Extension status should become **OK** on the assigned ActiveGate.

---

## Part 4 — Verify in Grail

Wait 1–2 minutes after saving, then run in **Notebooks** or **Logs**:

### 4.1 All extension logs

```dql
fetch logs, from: now()-15m
| filter `log.source` == "mongodb_custom_query"
| fields timestamp, `query.name`, `query.status`, `error.message`
| sort timestamp desc
| limit 30
```

**Success:** `query.status` = `success` for names like `connection_test`, `orders_by_status`, etc.

### 4.2 Order counts by status

```dql
fetch logs, from: now()-15m
| filter `query.name` == "orders_by_status"
| fields timestamp, `result.status`, `result.order_count`, `result.total_amount`
```

### 4.3 Confirm pipeline source (optional)

```dql
fetch logs, from: now()-15m
| filter `dt.openpipeline.source` == "extension:custom:mongodb-query"
| summarize count(), by: {`query.name`}
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Upload `403` / insufficient scope | API token missing `extensions.write` (see [API token scopes](#api-token-scopes)) |
| Upload URL/host error | Use the `live.dynatrace.com` env URL, not `apps.dynatrace.com` |
| Extension upload / schema errors | Rebuild and upload **0.1.30** after any schema change |
| Signature failed on ActiveGate | Vault `ca.pem` must match AG `root.pem`; clear extension cache if needed |
| Invalid pipeline JSON | Paste a JSON array of stages, not shell expressions |
| No logs in Grail | Check AG assignment, DB connectivity, extension health in Hub |
| Logs only once per query | Dedup + `send_on_change` — expected when data unchanged; set **Respect frequency_minutes** = `false` while debugging |
| Constraint error on save | Fill all required endpoint fields; use queries list **or** queries JSON (not both broken) |
| Not authorized to read collection | Grant the monitoring account the `read` role on the configured database |

---

## Rebuild after code changes

```bash
source .venv/bin/activate
./scripts/build_custom_integration.sh
./scripts/upload_extension.sh
```

Update the monitoring configuration to the new version in the Dynatrace UI.

MongoDB connection settings: set **Authentication database** to the database where the user was created (often `admin`); blank uses **Database name**. TLS fields are hidden from the monitoring UI; existing TLS configuration values remain supported by the runtime. Host accepts a hostname or MongoDB connection URI. Credentials stay in Credential Vault or the existing secret field. Do not put passwords in the host field.

The collector keeps the existing frequencies, dedup modes, row limits, result modes and log delivery. SQLite remains the local state store. MongoDB aggregation stages `$out` and `$merge` are rejected. BSON ObjectId and Decimal128 values become strings; nested values retain the existing JSON-string formatting. The connection is checked with `ping` before running queries.

`connection_test` reads one demo document; an empty collection produces the existing zero-row summary. `orders_last_hour` returns no documents when there are no matching orders, also producing the zero-row summary.

## Dependency packaging (0.1.40)

The SDK defaults to CPython 3.10. `custom_integration/build.sh` explicitly downloads
binary dependencies for CPython **3.10 and 3.14**, for Linux **x86_64 and aarch64**.
Both architectures are bundled because the build machine does not establish the
remote ActiveGate architecture. Verify the target with `uname -m` on ActiveGate;
these bundles cover `x86_64` and `aarch64`. The PyMongo constraint remains
`>=4.6,<5.0`; the SDK downloads its transitive dependencies for each target.
The existing assemble/sign workflow regenerates the signature after downloading.

Build the signed package:

```bash
./scripts/build_custom_integration.sh
```

Validate the final signed package on a Linux CPython 3.14 host (ideally ActiveGate's
architecture). This creates a clean temporary environment, installs only from the
ZIP's bundled wheels using `--no-index`, checks dependency consistency, imports the
extension and PyMongo native modules, and checks offline resolution for all four
bundled runtime/architecture combinations:

```bash
python3.14 scripts/validate_offline_package.py custom_integration/dist/custom_mongodb-query-0.1.40.zip
```

Cross-platform pip resolution checks do not execute native code for the other CPU
architecture. Run the same validation on that architecture for a native import test.
