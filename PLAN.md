# RailPulse Ops — Implementation Plan

Predictive track-health monitoring for Indian Railways: trackside IoT sensors (vibration, rail temperature,
ultrasonic, point-machine) stream telemetry to AWS, an anomaly-detection tier flags structural
micro-anomalies, and a regional-language escalation workflow reaches gangmen before failures escalate.

## 1. Architecture

```
 Trackside sensors (vibration / temperature / ultrasonic / point machine)
        │  MQTT over TLS, X.509 cert per sensor
        ▼
 ┌──────────────┐  IoT Rule  ┌──────────────────┐
 │ AWS IoT Core │──────────▶│ SQS telemetry-q  │──▶ DLQ
 └──────┬───────┘  fan-out   └────────┬─────────┘
        │                             │ sqsd daemon
        ▼                             ▼
 Amazon Data Firehose ─▶ S3   ┌──────────────────────────┐
 (raw lake, partitioned)      │ EB WORKER env (Docker)   │
        │                     │ FastAPI /process         │
   Glue Catalog + Athena      │ rules + z-score +        │
                              │ IsolationForest          │
                              └───┬───────────────┬──────┘
                       writes     │               │ anomaly
                                  ▼               ▼
                       ┌────────────────┐   EventBridge (custom bus)
                       │   DynamoDB     │         │
                       │ Telemetry/     │         ▼
                       │ Incidents/     │   Step Functions escalation
                       │ Assets/        │   ├ Translate → regional lang
                       │ Baselines      │   ├ SNS / End User Messaging SMS
                       └───────┬────────┘   ├ Polly → voice clip
                               │            └ wait ACK → escalate SSE → DEN
                               ▼
 Next.js dashboard ◀── ALB + WAF ◀── EB WEB env (FastAPI API, CPU > 70% scaling)
 (Amplify Hosting)        Cognito auth · Location Service maps · SSE live feed

 Cross-cutting: CloudWatch + X-Ray · ECR + CodePipeline/CodeBuild · KMS · Secrets Manager
 EventBridge Scheduler → Lambda "sensor heartbeat" (a silent sensor is itself an alert)
```

Region: **ap-south-1 (Mumbai)**, optional DR in **ap-south-2 (Hyderabad)** — keeps data in India.

## 2. AWS services

| # | Service | Role |
|---|---|---|
| 1 | Elastic Beanstalk | Two Docker envs: **Web** (API) + **Worker** (detection) |
| 2 | EC2 + Auto Scaling | Web: CPU > 70%. Worker: SQS backlog-per-instance |
| 3 | Application Load Balancer | Health checks, rolling deploys, SSE |
| 4 | AWS IoT Core | MQTT ingest, per-sensor certs, Rules Engine |
| 5 | Amazon SQS (+ DLQ) | Decouple ingest from detection; poison messages → DLQ after 5 tries |
| 6 | Amazon DynamoDB | Telemetry (TTL), Incidents, Assets, Baselines |
| 7 | Amazon S3 | Raw data lake, training data, model artifacts |
| 8 | Amazon Data Firehose | IoT → S3, partitioned by division/date |
| 9 | Glue Data Catalog + Athena | SQL over historical telemetry |
| 10 | Amazon EventBridge | `AnomalyDetected` events; Scheduler for heartbeats |
| 11 | AWS Step Functions | Escalation: gangman → wait ACK → SSE → DEN |
| 12 | AWS Lambda | Glue: heartbeat checker, ACK webhook, notification formatter |
| 13 | SNS + AWS End User Messaging SMS | SMS/push (India requires TRAI DLT registration) |
| 14 | Amazon Translate | Hindi, Tamil, Telugu, Bengali, Marathi alerts |
| 15 | Amazon Polly | Voice alerts for feature-phone users |
| 16 | Amazon Cognito | Auth + RBAC groups (Admin, DEN, SSE, Gangman) |
| 17 | Amazon Location Service | Track-segment map, nearest gang to incident |
| 18 | CloudWatch + X-Ray | Logs, metrics, alarms (DLQ > 0, queue age, offline sensors), tracing |
| + | ECR, CodePipeline, CodeBuild | Container registry + CI/CD to EB |
| + | AWS WAF, KMS, Secrets Manager, IAM | Security layer |
| opt | SageMaker, Amplify Hosting, QuickSight, IoT Greengrass | Stretch goals |

Avoid: Timestream for LiveAnalytics (closed to new customers), Amazon Pinpoint (end of support Oct 2026),
Lookout for Equipment (discontinued). EB "Multi-container Docker" platform is retired — use
**Docker on Amazon Linux 2023** with `docker-compose.yml`.

## 3. Data model (DynamoDB, on-demand)

| Table | PK | SK | Notes |
|---|---|---|---|
| Telemetry | `SENSOR#<id>` | `TS#<epoch_ms>` | TTL 30 days via `expires_at`; long-term copy lives in S3 |
| Incidents | `SEG#<segment_id>` | `INC#<ts>#<type>` | GSI1 `DIV#<division>` / `STATUS#<status>#<ts>`; conditional writes for dedupe |
| Assets | `SEG#<id>` | `META` or `SENSOR#<id>` | km markers, lat/lon, division, stress-free temperature |
| Baselines | `SENSOR#<id>` | `METRIC#<name>` | Rolling mean/std for z-scores |

SQS is at-least-once → all incident writes are idempotent (`attribute_not_exists`).

## 4. Anomaly detection (Worker)

1. **Physics rules** — ultrasonic back-wall echo drop > 6 dB → internal flaw; rail temp far below
   stress-free temperature → fracture risk (winter nights); far above → buckling risk;
   point-machine throw time / motor current drift → switch failure.
2. **Per-sensor statistics** — rolling z-score on vibration RMS and kurtosis.
3. **ML** — IsolationForest trained on S3 data, baked into the worker image (SageMaker later).

Fused into a **severity score (P1–P4)** that drives escalation speed and audience.

## 5. API (FastAPI Web env)

As built (Phase 4), all under `/api`:

```
GET  /health                          ALB health check (public)
GET  /config                          dashboard bootstrap: Cognito + map style (public)
GET  /me                              roles, divisions, canResolve
GET  /divisions/{div}/incidents?status=OPEN|ACKNOWLEDGED|RESOLVED
GET  /divisions/{div}/map             segments + worst active severity each
GET  /incidents/{id}                  incident + escalation timeline + segment
POST /incidents/{id}/acknowledge
POST /incidents/{id}/resolve          SSE / DEN / Admin only; frees the segment for new incidents
GET  /segments/{id}/telemetry?hours=6
GET  /stream/incidents?division=      Server-Sent Events (fetch-streamed with bearer token)
```

SMS ack links live on the alerting stack's Function URL (Phase 3); the worker's `/process` is on the
worker env only. Phase 5 added `GET /divisions/{div}/sensor-health` (heartbeat snapshot).

## 6. Elastic Beanstalk config

- Platform: Docker on Amazon Linux 2023.
- **Web env**: load-balanced, min 2 / max 8, CPU > 70% out / < 30% in, *Rolling with additional batch*
  or *Immutable* deployments, enhanced health, ALB idle timeout ~120 s for SSE.
- **Worker env**: `telemetry-q`, HTTP path `/process`, visibility timeout > worst-case inference;
  custom `.ebextensions` scaling policy on `ApproximateNumberOfMessagesVisible` (EB's built-in
  triggers can't target SQS metrics).
- Private subnets + VPC endpoints for SQS/DynamoDB/S3; least-privilege instance profiles per env.

## 7. CI/CD

GitHub → CodePipeline → CodeBuild (tests, docker build, push ECR, bundle) → EB staging → approval → EB prod.

## 8. Phases

| Phase | Scope | Exit criteria |
|---|---|---|
| 0. Foundations | Account, budgets, IAM, repo, CDK skeleton | `cdk deploy` creates core stack |
| 1. Ingest | IoT Core certs, simulator, IoT Rule → SQS + Firehose → S3 | Simulated messages land in SQS and S3 |
| 2. Detect | Worker env, rules + z-score, DynamoDB writes, DLQ, idempotency | Injected crack → exactly one P1 incident |
| 3. Alert | EventBridge → Step Functions → Translate/Polly/SMS, ACK, escalation | Hindi SMS received; no ACK in 10 min → escalates |
| 4. API + Dashboard | Web env, Cognito, SSE, Location map, charts | Live incident on map within seconds |
| 5. Scale + Harden | Scaling policies, WAF, KMS, alarms, X-Ray, load test | Visible scale-out during burst, zero lost messages |
| 6. Analytics + Polish | Glue/Athena, CI/CD, demo script | One-click deploy + 5-min demo |

## 9. Demo script

1. Map shows 3 healthy divisions.
2. Simulator peak-transit burst → queue depth rises, worker env scales out, backlog drains.
3. Inject ultrasonic flaw at a segment → P1 incident live; Tamil/Hindi SMS + Polly voice clip.
4. Don't ACK → Step Functions escalates to Section Engineer.
5. ACK → incident shows *Acknowledged* (lamps keep showing severity).
6. Athena: "fracture alerts by hour, winter nights".

## 10. Risks

| Risk | Mitigation |
|---|---|
| Hot DynamoDB partition | Per-sensor keys; edge aggregation (1 s windows) |
| Duplicate / poison messages | Idempotent writes, DLQ + alarm, replay script |
| Sensors offline | Heartbeat checker; Greengrass buffering (stretch) |
| India SMS delivery | Register DLT early; Polly voice fallback |
| Alert fatigue | Severity tiers, per-sensor baselines, confirmation window for P3/P4 |
| Cost | t3.small, on-demand DDB, TTL, S3 lifecycle, AWS Budgets |
