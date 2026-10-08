"""DynamoDB access. Key layouts follow PLAN.md section 3."""

import json
import time
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from botocore.exceptions import ClientError

from .detection import Finding, SegmentInfo
from .schema import TelemetryMessage
from .stats import RunningStat, SensorStats


def to_item(obj: Any) -> Any:
    """DynamoDB rejects Python floats; round-trip through JSON to turn them into Decimals."""
    return json.loads(json.dumps(obj), parse_float=Decimal)


def _is_conditional_failure(e: ClientError) -> bool:
    return e.response["Error"]["Code"] == "ConditionalCheckFailedException"


class TelemetryRepo:
    def __init__(self, table, ttl_days: int) -> None:
        self._table = table
        self._ttl_s = ttl_days * 86400

    def put(self, msg: TelemetryMessage) -> None:
        # Re-putting an identical item is harmless, so SQS redelivery needs no special casing.
        self._table.put_item(Item=to_item({
            "pk": f"SENSOR#{msg.sensor_id}",
            "sk": f"TS#{msg.ts:013d}",
            "segment_id": msg.segment_id,
            "division": msg.division,
            "type": msg.type,
            "seq": msg.seq,
            "metrics": msg.metrics,
            "battery_pct": msg.battery_pct,
            "expires_at": msg.ts // 1000 + self._ttl_s,
        }))


class BaselineRepo:
    """One item per sensor. Concurrent workers may overwrite each other's update; for an
    EWMA that only means an occasional skipped sample, which is cheaper than locking."""

    def __init__(self, table) -> None:
        self._table = table

    def load(self, sensor_id: str) -> SensorStats:
        item = self._table.get_item(Key={"pk": f"SENSOR#{sensor_id}", "sk": "STATS"}).get("Item")
        if not item:
            return {}
        return {k: RunningStat(float(v["mean"]), float(v["var"]), int(v["n"])) for k, v in item["stats"].items()}

    def save(self, sensor_id: str, stats: SensorStats) -> None:
        self._table.put_item(Item=to_item({
            "pk": f"SENSOR#{sensor_id}",
            "sk": "STATS",
            "stats": {k: {"mean": s.mean, "var": s.var, "n": s.n} for k, s in stats.items()},
            "updated_at": int(time.time()),
        }))


class AssetRepo:
    """Segment metadata changes rarely; cache it to save a read per message."""

    def __init__(self, table, ttl_s: float = 300) -> None:
        self._table = table
        self._ttl_s = ttl_s
        self._cache: dict[str, tuple[float, SegmentInfo | None]] = {}

    def get(self, segment_id: str) -> SegmentInfo | None:
        hit = self._cache.get(segment_id)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        item = self._table.get_item(Key={"pk": f"SEG#{segment_id}", "sk": "META"}).get("Item")
        info = None
        if item:
            sft = item.get("stress_free_temp_c")
            km = item.get("km_from")
            info = SegmentInfo(
                segment_id=segment_id,
                division=item["division"],
                stress_free_temp_c=float(sft) if sft is not None else None,
                km_from=float(km) if km is not None else None,
            )
        self._cache[segment_id] = (time.monotonic() + self._ttl_s, info)
        return info


class Change(StrEnum):
    CREATED = "created"
    ESCALATED = "escalated"
    UPDATED = "updated"


@dataclass(frozen=True)
class IncidentChange:
    kind: Change
    item: dict  # full incident item after the write


class IncidentRepo:
    """At most one ACTIVE incident per (segment, type).

    Layout in the Incidents table:
      SEG#<seg> / ACTIVE#<type>        pointer to the open incident (deleted on resolve, Phase 4)
      SEG#<seg> / INC#<ts>#<type>      the incident itself; GSI1 = DIV#<div> / STATUS#OPEN#<ts>

    Creating both in one conditional transaction is what makes "exactly one incident"
    hold under SQS redelivery and concurrent workers.
    """

    EVENT_CREATED = "AnomalyDetected"
    EVENT_ESCALATED = "IncidentEscalated"

    def __init__(self, table) -> None:
        self._table = table
        # The resource-layer client: takes plain Python values and (de)serialises them itself.
        self._client = table.meta.client
        self._name = table.name

    def record(self, f: Finding, msg: TelemetryMessage, segment: SegmentInfo | None) -> IncidentChange | None:
        seg_pk = f"SEG#{msg.segment_id}"
        pointer_key = {"pk": seg_pk, "sk": f"ACTIVE#{f.code}"}
        incident_sk = f"INC#{msg.ts:013d}#{f.code}"
        finding = self._finding_doc(f, msg)
        incident = to_item({
            "pk": seg_pk,
            "sk": incident_sk,
            "incident_id": f"{msg.segment_id}:{f.code}:{msg.ts}",
            "type": f.code,
            "status": "OPEN",
            "severity": int(f.severity),
            "segment_id": msg.segment_id,
            "division": msg.division,
            "km_from": segment.km_from if segment else None,
            "first_seen": msg.ts,
            "last_seen": msg.ts,
            "occurrences": 1,
            "first_finding": finding,
            "last_finding": finding,
            "pending_event": self.EVENT_CREATED,
            "gsi1pk": f"DIV#{msg.division}",
            "gsi1sk": f"STATUS#OPEN#{msg.ts:013d}",
        })
        incident["sensor_ids"] = {msg.sensor_id}

        for _ in range(3):
            try:
                self._client.transact_write_items(TransactItems=[
                    {"Put": {"TableName": self._name,
                             "Item": {**pointer_key, "incident_sk": incident_sk,
                                      "incident_id": incident["incident_id"]},
                             "ConditionExpression": "attribute_not_exists(pk)"}},
                    {"Put": {"TableName": self._name,
                             "Item": incident,
                             "ConditionExpression": "attribute_not_exists(pk)"}},
                ])
                return IncidentChange(Change.CREATED, incident)
            except ClientError as e:
                if e.response["Error"]["Code"] != "TransactionCanceledException":
                    raise
                codes = [r.get("Code") for r in e.response.get("CancellationReasons", [])]

            if codes and codes[0] == "ConditionalCheckFailed":
                pointer = self._table.get_item(Key=pointer_key, ConsistentRead=True).get("Item")
                if pointer is None:
                    continue  # resolved between our two calls — try creating again
                return self._touch(seg_pk, pointer["incident_sk"], f, msg, finding)
            if len(codes) > 1 and codes[1] == "ConditionalCheckFailed":
                return None  # stale redelivery of a reading whose incident was already resolved
            raise RuntimeError(f"unexpected transaction cancellation: {codes}")
        raise RuntimeError(f"incident pointer for {pointer_key} kept changing")

    def _touch(self, seg_pk: str, incident_sk: str, f: Finding, msg: TelemetryMessage, finding: dict) -> IncidentChange:
        key = {"pk": seg_pk, "sk": incident_sk}
        item = self._table.update_item(
            Key=key,
            UpdateExpression="SET last_seen = :ts, last_finding = :f ADD occurrences :one, sensor_ids :sid",
            ExpressionAttributeValues={":ts": msg.ts, ":f": to_item(finding), ":one": 1, ":sid": {msg.sensor_id}},
            ReturnValues="ALL_NEW",
        )["Attributes"]

        if f.severity < int(item["severity"]):
            # Worse severity re-opens an acknowledged incident: a P2 ack must not silence a P1.
            try:
                item = self._table.update_item(
                    Key=key,
                    UpdateExpression=("SET severity = :s, escalated_at = :ts, pending_event = :e, "
                                      "#st = :open, gsi1sk = :g REMOVE escalation_state"),
                    ConditionExpression="severity > :s AND #st <> :resolved",
                    ExpressionAttributeNames={"#st": "status"},
                    ExpressionAttributeValues={
                        ":s": int(f.severity), ":ts": msg.ts, ":e": self.EVENT_ESCALATED,
                        ":open": "OPEN", ":resolved": "RESOLVED",
                        ":g": f"STATUS#OPEN#{int(item['first_seen']):013d}",
                    },
                    ReturnValues="ALL_NEW",
                )["Attributes"]
                return IncidentChange(Change.ESCALATED, item)
            except ClientError as e:
                if not _is_conditional_failure(e):
                    raise  # else another worker escalated it first
        return IncidentChange(Change.UPDATED, item)

    def clear_pending(self, item: dict, event: str) -> None:
        try:
            self._table.update_item(
                Key={"pk": item["pk"], "sk": item["sk"]},
                UpdateExpression="REMOVE pending_event",
                ConditionExpression="pending_event = :e",
                ExpressionAttributeValues={":e": event},
            )
        except ClientError as e:
            if not _is_conditional_failure(e):
                raise  # else a newer event replaced it; leave that one pending

    @staticmethod
    def _finding_doc(f: Finding, msg: TelemetryMessage) -> dict:
        return {
            "sensor_id": msg.sensor_id,
            "ts": msg.ts,
            "severity": int(f.severity),
            "metric": f.metric,
            "observed": f.observed,
            "expected": f.expected,
            "detail": f.detail,
        }
