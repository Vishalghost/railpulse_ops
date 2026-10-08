"""EventBridge Scheduler (every 5 min) → find sensors that stopped reporting.

Silence never reaches the worker on its own: no message, nothing to detect. So this checker
turns silence into a message: one synthetic `HB` per silent sensor onto the telemetry queue,
which the worker turns into SEGMENT_SILENT (P2) / SENSOR_SILENT (P4) incidents — reusing its
dedupe, escalation events and the SMS ladder unchanged.

It also writes a per-division health snapshot (DIV#<div> / HEALTH) for the dashboard.
"""

import json
import logging
import os
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import boto3
from boto3.dynamodb.conditions import Attr, Key

log = logging.getLogger()
log.setLevel(logging.INFO)

SILENT_AFTER_S = int(os.environ.get("SILENT_AFTER_S", "900"))
# Sensors quiet for longer than this are "not reporting" (decommissioned, never installed),
# listed on the dashboard but not paged every 5 minutes forever.
LOOKBACK_S = int(os.environ.get("LOOKBACK_S", "86400"))


def list_sensors(assets) -> list[dict]:
    items, kwargs = [], {"FilterExpression": Attr("sk").begins_with("SENSOR#")}
    while True:
        page = assets.scan(**kwargs)
        items += page["Items"]
        if "LastEvaluatedKey" not in page:
            return items
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def last_seen_ms(telemetry, sensor_id: str) -> int | None:
    resp = telemetry.query(KeyConditionExpression=Key("pk").eq(f"SENSOR#{sensor_id}"),
                           ScanIndexForward=False, Limit=1, ProjectionExpression="sk")
    return int(resp["Items"][0]["sk"][3:]) if resp["Items"] else None


def check(assets, telemetry, sqs, queue_url: str, now_ms: int, workers: int = 16) -> dict:
    sensors = list_sensors(assets)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        seen = dict(zip((s["sensor_id"] for s in sensors),
                        pool.map(lambda s: last_seen_ms(telemetry, s["sensor_id"]), sensors)))

    def state(sensor_id: str) -> str:
        ts = seen.get(sensor_id)
        if ts is None or now_ms - ts > LOOKBACK_S * 1000:
            return "not_reporting"
        return "silent" if now_ms - ts > SILENT_AFTER_S * 1000 else "ok"

    by_segment: dict[str, list[dict]] = defaultdict(list)
    for s in sensors:
        by_segment[s["pk"][4:]].append(s)

    messages, health = [], defaultdict(lambda: {"total": 0, "reporting": 0, "silent": [], "not_reporting": []})
    for segment_id, members in by_segment.items():
        states = {s["sensor_id"]: state(s["sensor_id"]) for s in members}
        # Whole segment dark (and at least one went dark recently) → likely cut or theft.
        segment_silent = "silent" in states.values() and "ok" not in states.values()
        for s in members:
            st, div = states[s["sensor_id"]], s["division"]
            h = health[div]
            h["total"] += 1
            if st == "ok":
                h["reporting"] += 1
            else:
                h[st].append(s["sensor_id"])
            if st == "silent":
                messages.append({
                    "v": 1, "division": div, "sensor_id": s["sensor_id"], "segment_id": segment_id,
                    "type": "HB", "ts": now_ms, "seq": 0, "ingest_ts": now_ms,
                    "metrics": {"silent_s": round((now_ms - seen[s["sensor_id"]]) / 1000),
                                "segment_silent": segment_silent},
                })

    for i in range(0, len(messages), 10):
        batch = messages[i:i + 10]
        resp = sqs.send_message_batch(QueueUrl=queue_url, Entries=[
            {"Id": str(n), "MessageBody": json.dumps(m)} for n, m in enumerate(batch)])
        if resp.get("Failed"):
            raise RuntimeError(f"SQS rejected {len(resp['Failed'])} heartbeat message(s): {resp['Failed'][:2]}")

    for div, h in health.items():
        assets.put_item(Item={"pk": f"DIV#{div}", "sk": "HEALTH", "checked_at": now_ms // 1000,
                              "total": h["total"], "reporting": h["reporting"],
                              "silent": sorted(h["silent"]), "not_reporting": sorted(h["not_reporting"])})

    summary = {div: {"total": h["total"], "reporting": h["reporting"], "silent": len(h["silent"])}
               for div, h in health.items()}
    log.info("heartbeat: %d sensors, %d silent message(s) queued, %s", len(sensors), len(messages), summary)
    return {"sensors": len(sensors), "queued": len(messages), "divisions": summary}


@lru_cache(maxsize=1)
def _deps():
    region = os.environ.get("AWS_REGION")
    ddb = boto3.resource("dynamodb", region_name=region)
    return (ddb.Table(os.environ["ASSETS_TABLE"]), ddb.Table(os.environ["TELEMETRY_TABLE"]),
            boto3.client("sqs", region_name=region))


def handler(_event, _context):
    assets, telemetry, sqs = _deps()
    return check(assets, telemetry, sqs, os.environ["TELEMETRY_QUEUE_URL"], int(time.time() * 1000))
