# RailPulse Ops

Predictive track-health monitoring for Indian Railways on AWS.
[ARCHITECTURE.md](ARCHITECTURE.md) — how it fits together · [DEMO.md](DEMO.md) — 5-minute run sheet ·
[PLAN.md](PLAN.md) — original design.

**Status:** all phases (0–6) built and verified locally — nothing deployed yet. `dev` deploys in
**lean** mode (no NAT gateway / interface endpoints) to fit a small budget; `prod` is fully private.

```
infra/        Python CDK
              ├ RailPulse-<stage>-Core      VPC (lean or private), KMS key, IoT rule, SQS + DLQ,
              │                             Firehose → S3, DynamoDB, EventBridge bus, ops SNS topic + alarms
              ├ RailPulse-<stage>-Worker    Elastic Beanstalk worker env (Docker, AL2023)
              ├ RailPulse-<stage>-Alerting  EventBridge rule → Step Functions escalation → Lambda (Translate,
              │                             Polly, End User Messaging SMS, SNS) + ack page (Function URL),
              │                             heartbeat checker (EventBridge Scheduler, every 5 min)
              ├ RailPulse-<stage>-Edge      WAF web ACL for CloudFront (us-east-1)
              ├ RailPulse-<stage>-Web       EB web env (ALB, CPU>70% scaling, Compose: nginx + FastAPI),
              │                             CloudFront (HTTPS, WAF, origin secret), Cognito, Location map key
              ├ RailPulse-<stage>-Ops       CloudWatch dashboard, AWS Budgets alert ($50 default)
              ├ RailPulse-<stage>-Analytics Glue tables (partition projection), Athena workgroup + saved
              │                             queries, detection events → S3 via Firehose
              └ RailPulse-<stage>-Pipeline  (opt-in) GitHub → CodePipeline → CodeBuild test → deploy
analytics/    Saved Athena queries (*.sql), validated offline against the Glue schema
scripts/      demo.ps1 — drives the live demo timeline
worker/       FastAPI detection service — sqsd POSTs each telemetry message to /process
alerting/     Escalation Lambdas, state machine (ASL/JSONata) and escalation policy
api/          FastAPI dashboard API — Cognito JWT auth, division scoping, SSE live feed
dashboard/    Next.js (static export) control-room dashboard: mimic-panel track strip, map, incidents
webproxy/     nginx: serves the dashboard export, proxies /api to FastAPI
tools/        create_user.py — add dashboard users to Cognito role/division groups
loadtest/     Locust script for the dashboard API
simulator/    MQTT trackside-sensor simulator (3 divisions, 210 sensors) with fault injection
```

### Web request path

```
browser ─HTTPS─▶ CloudFront + WAF ─▶ ALB (public subnet) ─▶ EC2 (private): nginx ─┬─ /       dashboard
                  /api/* uncached     SG: CloudFront ranges only    X-Origin-Verify  └─ /api/*  FastAPI ─▶ DynamoDB
```
Two layers keep the ALB from being reached around WAF: its security group admits only CloudFront's
origin-facing prefix list, and nginx rejects requests without the secret `X-Origin-Verify` header
(from Secrets Manager) — the prefix list alone would admit anyone's CloudFront distribution.
Access = Cognito groups: a role (`Admin`, `DEN`, `SSE`, `Gangman`) plus divisions (`DIV_DLI`, …).
Admins see every division; only SSE, DEN and Admin can resolve incidents.

### Alert flow

```
AnomalyDetected / IncidentEscalated (P1–P3)
  → start Lambda: build ladder from DIV#<div>/CONTACT# items, StartExecution (name = incident + severity → dedupe)
  → state machine, per rung:  CheckIncident (still OPEN at this severity?) → Notify (.waitForTaskToken)
       notify Lambda: Translate → SMS per contact (own single-use ack link) + Polly clip + SNS topic
       ack page: GET shows alert + audio (no side effects) · POST → SendTaskSuccess, incident ACKNOWLEDGED
       rung timeout → next rung (GANGMAN → SSE → DEN); ladder exhausted → escalation_state UNACKNOWLEDGED
```

Escalation timings live in [alerting/railpulse_alerting/policy.py](alerting/railpulse_alerting/policy.py)
(P1: 10/10/15 min, P2: 30/60 min, P3: inform SSE only).

## Quickstart (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r infra\requirements-dev.txt -r simulator\requirements.txt -r worker\requirements-dev.txt `
            -r alerting\requirements-dev.txt -r api\requirements-dev.txt

# 0. Build the dashboard (the web stack bundles dashboard/out into the nginx image)
cd dashboard; npm install; npm run build; cd ..

# 1. Deploy all stacks (app in ap-south-1 by default; WAF stack always in us-east-1)
cd infra
cdk bootstrap aws://<ACCOUNT>/ap-south-1 aws://<ACCOUNT>/us-east-1   # once per account
cdk deploy --all -c ops_email=you@example.com     # ops alarms + budget alerts go here
# Demo-friendly alerting: email copy of every page, SMS API validation only, 60 s per rung
cdk deploy --all -c ops_email=you@example.com -c alert_email=you@example.com `
                 -c sms_mode=dry_run -c ack_timeout_override_s=60
# Other knobs: -c budget_usd=50  -c lean=false (private subnets + NAT)  -c silent_after_s=120  -c stage=prod

# 2. Provision the simulator certificate and seed segments, sensors and contacts
cd ..\simulator
python provision.py
python seed_assets.py                              # placeholder (unroutable) numbers
python seed_assets.py --demo-phone +9198XXXXXXXX   # every page goes to your phone

# 2b. Create dashboard users, then open the DashboardUrl output (RailPulse-dev-Web stack)
cd ..
python tools\create_user.py --username admin --email you@example.com --role Admin
python tools\create_user.py --username sse.delhi --email sse@example.com --role SSE --division DLI
cd simulator

# 3. Stream telemetry
python -m railpulse_sim --dry-run --duration 10 --limit 5    # local, no AWS
python -m railpulse_sim --interval 5                          # live to IoT Core
python -m railpulse_sim --fault crack@SEG-DLI-012+150         # crack after baselines warm up (~100 s)
python -m railpulse_sim --burst-at 60 --burst-for 120         # peak-transit burst (5× rate)
```

## Tests

```powershell
cd simulator; python -m pytest     # fleet, reading models, fault parsing
cd ..\worker; python -m pytest     # detection rules + end-to-end /process against moto DynamoDB
cd ..\alerting; python -m pytest   # escalation policy, messages, start/notify/ack handlers
cd ..\api; python -m pytest        # JWT auth, division scoping, ack/resolve, map, telemetry, live feed
cd ..\infra; python -m pytest      # synthesized-template assertions (lean + prod builds): KMS, network,
                                   # WAF, origin lock, IMDS, scaling, alarms, budget; Athena SQL parsed
                                   # and checked against the Glue schema; pipeline + buildspec
cd ..\dashboard; npx tsc --noEmit; npm run build
```

## Dashboard local development

```powershell
# API against the deployed tables, with a real Cognito client (localhost:3000 is an allowed callback)
cd api
$env:INCIDENTS_TABLE="<IncidentsTableName>"; $env:ASSETS_TABLE="<AssetsTableName>"; $env:TELEMETRY_TABLE="<TelemetryTableName>"
$env:USER_POOL_ID="<UserPoolId>"; $env:USER_POOL_CLIENT_ID="<UserPoolClientId>"; $env:COGNITO_DOMAIN="<CognitoDomain>"
$env:CORS_ORIGINS="http://localhost:3000"
uvicorn app.main:app --port 8000

cd ..\dashboard
$env:NEXT_PUBLIC_API_BASE="http://localhost:8000"; npm run dev    # http://localhost:3000
```

## Verifying Phase 4 live

1. Open `DashboardUrl`, sign in with a user from `create_user.py`, set a new password.
2. The track strip shows 22 signals for the division; run the simulator with a crack fault — within a few
   seconds the segment's signal turns red and flashes, and the incident appears in *Active incidents*
   without a reload (feed shows **Live**).
3. Open the incident: escalation ladder fills in as rungs are paged; charts show the back-wall echo drop.
4. *Acknowledge* (anyone) → green *Acknowledged* chip, list stops flashing (the lamp keeps showing
   severity); *Resolve* (SSE/DEN/Admin) → signal returns to green.
5. Scale-out: `python -m railpulse_sim --burst-at 30 --burst-for 300`, watch the web and worker
   environments' instance counts in the EB console.

## Verifying Phase 5 live

1. **Origin lock:** `curl http://<WebEnvironmentUrl>/` from your machine times out (security group);
   the `DashboardUrl` works. Inside the VPC, a request without `X-Origin-Verify` gets 403 from nginx.
2. **WAF:** CloudWatch → WAF → `railpulse-dev-dashboard` shows sampled requests; hammering `/api/*`
   past 3000 requests / 5 min from one IP returns 403.
3. **Silent sensors:** run the simulator with `--fault silent@SEG-DLI-007+60` (needs no `apply_fault()`).
   The `+60` matters: the segment must report first — sensors that have *never* reported are listed,
   not paged. 15 min after it goes dark (`SILENT_AFTER_S`, plus up to 5 min until the next check) the
   segment gets a P2 *Segment gone silent* incident and the gangman is paged;
   the dashboard header shows "… · 3 gone silent".
4. **Scaling dashboard:** CloudWatch → Dashboards → `railpulse-dev` during a burst — backlog rises,
   *Instances reporting OK* steps up, backlog drains.
5. **Load test:** `locust -f loadtest/locustfile.py --host <DashboardUrl>` (see the file header).
6. **Ops alarms:** confirm the SNS email subscription; stop the worker env's instances briefly and the
   *TelemetryQueueStale* alarm emails you.

## SMS in India

`sms_mode` is `off` by default. `dry_run` calls the AWS End User Messaging API with `DryRun=true`
(validates, sends nothing). `live` needs: an End User Messaging sender ID, and **TRAI DLT registration**
(entity ID + per-template IDs) — set `SMS_ORIGINATION_ID`, `IN_DLT_ENTITY_ID`, `IN_DLT_TEMPLATE_ID` on the
notify Lambda. DLT templates are matched literally, so each language's message needs its own registered
template. Until then the SNS topic (email) carries every page, and the ack page works from any link.

## Verifying Phase 2 live

1. Run the simulator with `--fault crack@SEG-DLI-012+150` (needs `apply_fault()` implemented).
2. Worker logs (EB console → *Logs*, or CloudWatch `/aws/elasticbeanstalk/railpulse-dev-worker/…`) show
   `{"event": "findings", ... "CRACK_SUSPECTED" ...}`.
3. Incidents table: exactly one `SEG#SEG-DLI-012` / `INC#…#CRACK_SUSPECTED` item, `severity` 1 or 2,
   `occurrences` climbing; plus an `ACTIVE#CRACK_SUSPECTED` pointer.
4. EventBridge → *Archives* → the bus archive has an `AnomalyDetected` event.

## Verifying Phase 3 live

1. Deploy with `-c alert_email=… -c ack_timeout_override_s=60` and confirm the SNS subscription email.
2. Trigger a crack (above). Within seconds: a Step Functions execution `SEG-…-CRACK_SUSPECTED-…-P1`,
   an email per rung, and `NOTIFY#…` items under the incident.
3. Open the ack link from the `ACK#…` item (or the SMS) → Hindi alert + voice clip → tap confirm →
   execution ends in *Acknowledged*, incident `status = ACKNOWLEDGED`.
4. Don't ack → after 60 s the SSE rung is paged, then DEN; finally the execution fails with
   `Unacknowledged` and the incident gets `escalation_state = UNACKNOWLEDGED`.

Detection warms up per sensor: z-score and back-wall-drop rules stay silent for the first 20 readings
(~100 s at a 5 s interval). Absolute limits (kurtosis, temperature vs stress-free, switch) fire immediately.

## Teardown

```powershell
cd simulator; python provision.py --teardown   # detach cert first, or the IoT policy blocks deletion
cd ..\infra;  cdk destroy --all                # includes the us-east-1 Edge (WAF) stack
```

## Analytics (Athena)

Athena console → workgroup `railpulse-<stage>` → *Saved queries*. Five are installed from
[analytics/queries/](analytics/queries/): fracture risk by hour, coldest nights vs stress-free
temperature, sensor reporting gaps, incidents by hour, escalation rate. Tables use partition
projection (no crawler), and the workgroup refuses any query that would scan more than 1 GB.
Add a query by dropping a `.sql` file with `-- name:` / `-- description:` headers into that folder;
`infra` tests parse it and check every column against the Glue schema.

## CI/CD (optional)

1. Push this repo to GitHub.
2. Console → *Developer Tools → Settings → Connections* → create a GitHub connection and finish
   the authorisation handshake (CloudFormation can't do this step).
3. Deploy the pipeline once by hand:
   `cdk deploy RailPulse-dev-Pipeline -c github_repo=<owner>/<repo> -c codeconnection_arn=<arn> -c ops_email=…`
4. From then on every push to `main` runs all tests, builds the dashboard, synthesizes, then
   `cdk deploy --all` ([buildspec.yml](buildspec.yml)). `prod` adds a manual approval step.
   Failed runs email the ops topic.

## Cost notes ($50 budget)

- **Always-on in lean `dev`:** two t3.small instances (web + worker), one ALB, the WAF web ACL and
  its rule groups, plus small fixed items (KMS key, one secret, CloudWatch custom metrics and
  dashboard). There's no NAT gateway or interface endpoint; that's what lean mode removes.
- **Usage-based:** IoT messages, DynamoDB requests, Firehose, Lambda, Athena scans and SMS. The
  full fleet at the default 5 s interval sends ~43 msg/s (~3.7 M/day). `demo.ps1` stops after
  40 min; use `--limit`/`--duration` otherwise.
- Price the always-on set for ap-south-1 in the AWS Pricing Calculator before deploying, and plan
  on **deploy → demo → `cdk destroy --all`** rather than leaving it up. Pass `-c ops_email=…` so the
  $50 Budgets alert (80% actual, 100% forecast, account-wide) reaches you. It warns; it doesn't stop.
- `cdk destroy --all` removes everything except the KMS key (7-day deletion window) and resources
  in a `prod` stage (retained).
