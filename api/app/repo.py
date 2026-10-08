"""Read/write access for the dashboard. Key layouts follow PLAN.md section 3 and
worker/app/store.py (incidents) / simulator/seed_assets.py (assets)."""

import re
import time
from decimal import Decimal

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

ACTIVE_STATUSES = ("OPEN", "ACKNOWLEDGED")
STATUSES = ACTIVE_STATUSES + ("RESOLVED",)
INTERNAL_KEYS = {"pk", "sk", "gsi1pk", "gsi1sk", "pending_event"}
INCIDENT_ID_RE = re.compile(r"^(SEG-[A-Z]{2,4}-\d{3}):([A-Z_]+):(\d{13})$")


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


def plain(obj):
    """DynamoDB Decimals/sets → JSON-native numbers/lists (FastAPI would emit Decimals as strings)."""
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    if isinstance(obj, set):
        return sorted(plain(v) for v in obj)
    if isinstance(obj, dict):
        return {k: plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [plain(v) for v in obj]
    return obj


def public(item: dict) -> dict:
    return plain({k: v for k, v in item.items() if k not in INTERNAL_KEYS})


def incident_key(incident_id: str) -> dict:
    m = INCIDENT_ID_RE.match(incident_id)
    if not m:
        raise NotFound(incident_id)
    segment, type_, ts = m.groups()
    return {"pk": f"SEG#{segment}", "sk": f"INC#{ts}#{type_}"}


class Repo:
    def __init__(self, incidents, assets, telemetry) -> None:
        self.incidents = incidents
        self.assets = assets
        self.telemetry = telemetry

    # --- incidents ------------------------------------------------------------------------
    def list_incidents(self, division: str, status: str, limit: int = 100) -> list[dict]:
        resp = self.incidents.query(
            IndexName="gsi1",
            KeyConditionExpression=Key("gsi1pk").eq(f"DIV#{division}") & Key("gsi1sk").begins_with(f"STATUS#{status}#"),
            ScanIndexForward=False,  # newest first
            Limit=limit,
        )
        return [public(i) for i in resp["Items"]]

    def active_incidents(self, division: str) -> list[dict]:
        return [i for s in ACTIVE_STATUSES for i in self.list_incidents(division, s, limit=500)]

    def get_incident(self, incident_id: str) -> dict:
        item = self.incidents.get_item(Key=incident_key(incident_id), ConsistentRead=True).get("Item")
        if not item:
            raise NotFound(incident_id)
        return item

    def timeline(self, incident: dict) -> list[dict]:
        resp = self.incidents.query(
            KeyConditionExpression=Key("pk").eq(incident["pk"]) & Key("sk").begins_with("NOTIFY#"),
            FilterExpression=Attr("incident_id").eq(incident["incident_id"]),
        )
        return [public(i) for i in resp["Items"]]

    def acknowledge(self, incident: dict, who: str) -> dict:
        return self._transition(incident, "OPEN", "ACKNOWLEDGED", {
            "acknowledged_by": who, "acknowledged_role": "DASHBOARD", "acknowledged_at": int(time.time())})

    def resolve(self, incident: dict, who: str, note: str | None) -> dict:
        """Close the incident and drop its ACTIVE# pointer, so the next detection on this
        segment opens a fresh incident instead of appending to a closed one."""
        now = int(time.time())
        client = self.incidents.meta.client
        try:
            client.transact_write_items(TransactItems=[
                {"Update": {
                    "TableName": self.incidents.name,
                    "Key": {"pk": incident["pk"], "sk": incident["sk"]},
                    "UpdateExpression": "SET #st = :r, resolved_by = :who, resolved_at = :t, resolution_note = :n, gsi1sk = :g",
                    "ConditionExpression": "#st <> :r",
                    "ExpressionAttributeNames": {"#st": "status"},
                    "ExpressionAttributeValues": {":r": "RESOLVED", ":who": who, ":t": now, ":n": note or "",
                                                  ":g": f"STATUS#RESOLVED#{int(incident['first_seen']):013d}"},
                }},
                {"Delete": {
                    "TableName": self.incidents.name,
                    "Key": {"pk": incident["pk"], "sk": f"ACTIVE#{incident['type']}"},
                    # Only if it still points at *this* incident.
                    "ConditionExpression": "attribute_not_exists(pk) OR incident_sk = :sk",
                    "ExpressionAttributeValues": {":sk": incident["sk"]},
                }},
            ])
        except ClientError as e:
            if e.response["Error"]["Code"] == "TransactionCanceledException":
                raise Conflict("incident already resolved or changed") from e
            raise
        return self.get_incident(incident["incident_id"])

    def _transition(self, incident: dict, from_status: str, to_status: str, extra: dict) -> dict:
        names = {"#st": "status"}
        values = {":from": from_status, ":to": to_status,
                  ":g": f"STATUS#{to_status}#{int(incident['first_seen']):013d}"}
        sets = ["#st = :to", "gsi1sk = :g"]
        for i, (k, v) in enumerate(extra.items()):
            sets.append(f"{k} = :x{i}")
            values[f":x{i}"] = v
        try:
            return self.incidents.update_item(
                Key={"pk": incident["pk"], "sk": incident["sk"]},
                UpdateExpression="SET " + ", ".join(sets),
                ConditionExpression="#st = :from",
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
                ReturnValues="ALL_NEW",
            )["Attributes"]
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise Conflict(f"incident is not {from_status}") from e
            raise

    # --- assets & telemetry -----------------------------------------------------------------
    def segments(self, division: str) -> list[dict]:
        resp = self.assets.query(
            KeyConditionExpression=Key("pk").eq(f"DIV#{division}") & Key("sk").begins_with("SEG#"))
        return [public(i) for i in resp["Items"]]

    def sensor_health(self, division: str) -> dict | None:
        """Snapshot written every 5 min by the alerting stack's heartbeat checker."""
        item = self.assets.get_item(Key={"pk": f"DIV#{division}", "sk": "HEALTH"}).get("Item")
        return public(item) if item else None

    def segment(self, segment_id: str) -> dict:
        item = self.assets.get_item(Key={"pk": f"SEG#{segment_id}", "sk": "META"}).get("Item")
        if not item:
            raise NotFound(segment_id)
        return public(item)

    def segment_sensors(self, segment_id: str) -> list[dict]:
        resp = self.assets.query(
            KeyConditionExpression=Key("pk").eq(f"SEG#{segment_id}") & Key("sk").begins_with("SENSOR#"))
        return [public(i) for i in resp["Items"]]

    def telemetry_for(self, sensor_id: str, from_ms: int, to_ms: int, limit: int) -> list[dict]:
        resp = self.telemetry.query(
            KeyConditionExpression=Key("pk").eq(f"SENSOR#{sensor_id}")
            & Key("sk").between(f"TS#{from_ms:013d}", f"TS#{to_ms:013d}"),
            ScanIndexForward=False,
            Limit=limit,
        )
        return [{"ts": int(i["sk"][3:]), "metrics": plain(i.get("metrics", {}))} for i in reversed(resp["Items"])]
