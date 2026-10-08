"""Dashboard/API load test.

    pip install locust
    $env:RAILPULSE_TOKEN = "<Cognito access token>"     # copy from the browser's sessionStorage
    $env:RAILPULSE_DIVISION = "DLI"
    locust -f loadtest/locustfile.py --host https://<DashboardUrl>

Each simulated user behaves like a control-room screen: one long-lived live feed plus
periodic map/incident refreshes and the occasional drill-down. Watch the web environment's
CPU trigger (> 70 %) add instances on the RailPulse-<stage> CloudWatch dashboard.

Mind the WAF rate rule (3000 /api requests per IP per 5 min): from a single machine,
keep users × request rate under ~10 req/s or WAF will (correctly) start returning 403s.
Ingest-side load is the simulator's job: `python -m railpulse_sim --burst-at 30 --burst-for 300`.
"""

import os
import random

from locust import HttpUser, between, task

TOKEN = os.environ["RAILPULSE_TOKEN"]
DIVISION = os.environ.get("RAILPULSE_DIVISION", "DLI")


class ControlRoomScreen(HttpUser):
    wait_time = between(2, 6)

    def on_start(self):
        self.client.headers["Authorization"] = f"Bearer {TOKEN}"
        self.incident_ids: list[str] = []

    @task(5)
    def map_and_incidents(self):
        self.client.get(f"/api/divisions/{DIVISION}/map", name="/api/divisions/[div]/map")
        r = self.client.get(f"/api/divisions/{DIVISION}/incidents?status=OPEN", name="/api/divisions/[div]/incidents")
        if r.ok:
            self.incident_ids = [i["incident_id"] for i in r.json()["items"]]

    @task(2)
    def drill_down(self):
        if not self.incident_ids:
            return
        iid = random.choice(self.incident_ids)
        r = self.client.get(f"/api/incidents/{iid}", name="/api/incidents/[id]")
        if r.ok:
            seg = r.json()["incident"]["segment_id"]
            self.client.get(f"/api/segments/{seg}/telemetry?hours=6", name="/api/segments/[id]/telemetry")

    @task(1)
    def sensor_health(self):
        self.client.get(f"/api/divisions/{DIVISION}/sensor-health", name="/api/divisions/[div]/sensor-health")
