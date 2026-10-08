"""End-to-end through FastAPI → repos → moto DynamoDB. This is the Phase 2 exit criterion:
an injected crack produces exactly one P1 incident, no matter how often SQS redelivers."""

import random

from boto3.dynamodb.conditions import Key

from app.stats import WARMUP_SAMPLES

T0 = 1_790_000_000_000


def uts(i: int, backwall: float) -> dict:
    return {"v": 1, "division": "DLI", "sensor_id": "DLI-001-UTS", "segment_id": "SEG-DLI-001",
            "type": "UTS", "ts": T0 + i * 5000, "seq": i, "battery_pct": 80.0, "ingest_ts": T0 + i * 5000,
            "metrics": {"backwall_echo_db": backwall, "flaw_echo_db": 12.0, "probe_coupling_ok": True}}


def incidents(table):
    return table.query(KeyConditionExpression=Key("pk").eq("SEG#SEG-DLI-001") & Key("sk").begins_with("INC#"))["Items"]


def warm_up(client, n: int = WARMUP_SAMPLES + 5) -> int:
    rng = random.Random(1)
    for i in range(n):
        assert client.post("/process", json=uts(i, 78.0 + rng.gauss(0, 0.4))).status_code == 200
    return n


def test_progressive_crack_yields_exactly_one_p1_incident(client, aws, publisher):
    i = warm_up(client)
    sent = []
    for backwall in (71.5, 71.0, 70.2, 66.0, 65.1, 64.0):  # P2 then P1 as the crack grows
        body = uts(i, backwall)
        assert client.post("/process", json=body).status_code == 200
        sent.append(body)
        i += 1
    # SQS at-least-once: replay two messages, including the very first crack reading.
    for body in (sent[0], sent[3]):
        assert client.post("/process", json=body).status_code == 200

    items = incidents(aws["incidents"])
    assert len(items) == 1
    inc = items[0]
    assert inc["type"] == "CRACK_SUSPECTED"
    assert inc["severity"] == 1
    assert inc["occurrences"] == len(sent) + 2
    assert "pending_event" not in inc
    assert inc["gsi1pk"] == "DIV#DLI"
    assert [t for t, _ in publisher.events] == ["AnomalyDetected", "IncidentEscalated"]

    # The crack readings never entered the baseline.
    stats = aws["baselines"].get_item(Key={"pk": "SENSOR#DLI-001-UTS", "sk": "STATS"})["Item"]["stats"]
    assert abs(float(stats["backwall_echo_db"]["mean"]) - 78.0) < 0.5


def test_escalation_reopens_an_acknowledged_incident(client, aws, publisher):
    i = warm_up(client)
    assert client.post("/process", json=uts(i, 71.0)).status_code == 200  # P2
    inc = incidents(aws["incidents"])[0]
    aws["incidents"].update_item(  # gangman acknowledges (alerting/handlers/ack.py)
        Key={"pk": inc["pk"], "sk": inc["sk"]},
        UpdateExpression="SET #st = :a, gsi1sk = :g",
        ExpressionAttributeNames={"#st": "status"},
        ExpressionAttributeValues={":a": "ACKNOWLEDGED", ":g": "STATUS#ACKNOWLEDGED#x"},
    )
    assert client.post("/process", json=uts(i + 1, 64.0)).status_code == 200  # crack grows → P1

    inc = incidents(aws["incidents"])[0]
    assert (inc["status"], inc["severity"]) == ("OPEN", 1)
    assert inc["gsi1sk"].startswith("STATUS#OPEN#")
    assert [t for t, _ in publisher.events] == ["AnomalyDetected", "IncidentEscalated"]


def test_event_outage_is_retried_not_lost(client, aws, publisher):
    i = warm_up(client)
    publisher.fail_next = 1
    assert client.post("/process", json=uts(i, 64.0)).status_code == 500  # sqsd will retry
    assert incidents(aws["incidents"])[0]["pending_event"] == "AnomalyDetected"
    assert publisher.events == []

    assert client.post("/process", json=uts(i, 64.0)).status_code == 200  # the retry
    assert [t for t, _ in publisher.events] == ["AnomalyDetected"]
    assert "pending_event" not in incidents(aws["incidents"])[0]


def test_telemetry_row_written_with_ttl(client, aws):
    assert client.post("/process", json=uts(0, 78.0)).status_code == 200
    row = aws["telemetry"].get_item(Key={"pk": "SENSOR#DLI-001-UTS", "sk": f"TS#{T0:013d}"})["Item"]
    assert row["expires_at"] == T0 // 1000 + 30 * 86400


def hb(sensor: str, segment_silent: bool, silent_s: float = 1200) -> dict:
    return {"v": 1, "division": "DLI", "sensor_id": sensor, "segment_id": "SEG-DLI-001", "type": "HB",
            "ts": T0, "seq": 0, "metrics": {"silent_s": silent_s, "segment_silent": segment_silent}}


def test_silent_segment_opens_one_p2_incident_without_touching_telemetry(client, aws, publisher):
    for sensor in ("DLI-001-VIB", "DLI-001-TMP", "DLI-001-UTS"):
        assert client.post("/process", json=hb(sensor, segment_silent=True)).status_code == 200

    [inc] = incidents(aws["incidents"])
    assert (inc["type"], inc["severity"], inc["occurrences"]) == ("SEGMENT_SILENT", 2, 3)
    assert inc["sensor_ids"] == {"DLI-001-VIB", "DLI-001-TMP", "DLI-001-UTS"}
    assert [t for t, _ in publisher.events] == ["AnomalyDetected"]
    assert aws["telemetry"].scan()["Count"] == 0   # heartbeats are not readings
    assert aws["baselines"].scan()["Count"] == 0


def test_single_silent_sensor_is_an_advisory(client, aws):
    assert client.post("/process", json=hb("DLI-001-UTS", segment_silent=False)).status_code == 200
    [inc] = incidents(aws["incidents"])
    assert (inc["type"], inc["severity"]) == ("SENSOR_SILENT", 4)


def test_malformed_message_rejected_for_dlq(client):
    bad = uts(0, 78.0) | {"segment_id": "not-a-segment"}
    assert client.post("/process", json=bad).status_code == 422
