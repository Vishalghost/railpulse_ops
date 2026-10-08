import json

from railpulse_alerting.handlers import heartbeat

NOW = 1_790_000_000_000
MIN = 60_000


class FakeSqs:
    def __init__(self):
        self.sent = []

    def send_message_batch(self, QueueUrl, Entries):
        assert len(Entries) <= 10
        self.sent += [json.loads(e["MessageBody"]) for e in Entries]
        return {"Successful": [{"Id": e["Id"]} for e in Entries]}


def seed(tables, telemetry):
    assets = tables["Assets"]
    for seg in ("SEG-DLI-001", "SEG-DLI-002", "SEG-DLI-003"):
        for t in ("VIB", "UTS"):
            sid = f"DLI-{seg[-3:]}-{t}"
            assets.put_item(Item={"pk": f"SEG#{seg}", "sk": f"SENSOR#{sid}", "sensor_id": sid,
                                  "type": t, "division": "DLI"})
    last = {
        "DLI-001-VIB": NOW - 1 * MIN, "DLI-001-UTS": NOW - 2 * MIN,       # healthy
        "DLI-002-VIB": NOW - 30 * MIN, "DLI-002-UTS": NOW - 31 * MIN,     # whole segment dark
        "DLI-003-VIB": NOW - 1 * MIN, "DLI-003-UTS": NOW - 40 * MIN,      # one sensor dark
    }
    for sid, ts in last.items():
        telemetry.put_item(Item={"pk": f"SENSOR#{sid}", "sk": f"TS#{ts - 5 * MIN:013d}"})
        telemetry.put_item(Item={"pk": f"SENSOR#{sid}", "sk": f"TS#{ts:013d}"})


def test_heartbeat_flags_dark_segment_and_lone_sensor(tables):
    import boto3
    telemetry = boto3.resource("dynamodb", region_name="ap-south-1").create_table(
        TableName="Telemetry", BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}])
    seed(tables, telemetry)
    # A sensor that has never reported is listed, not paged.
    tables["Assets"].put_item(Item={"pk": "SEG#SEG-DLI-004", "sk": "SENSOR#DLI-004-UTS",
                                    "sensor_id": "DLI-004-UTS", "type": "UTS", "division": "DLI"})
    sqs = FakeSqs()

    out = heartbeat.check(tables["Assets"], telemetry, sqs, "https://sqs/q", NOW, workers=2)

    by_sensor = {m["sensor_id"]: m for m in sqs.sent}
    assert set(by_sensor) == {"DLI-002-VIB", "DLI-002-UTS", "DLI-003-UTS"}
    assert by_sensor["DLI-002-VIB"]["metrics"] == {"silent_s": 1800, "segment_silent": True}
    assert by_sensor["DLI-003-UTS"]["metrics"]["segment_silent"] is False
    assert all(m["type"] == "HB" and m["division"] == "DLI" for m in sqs.sent)
    assert out["divisions"]["DLI"] == {"total": 7, "reporting": 3, "silent": 3}

    health = tables["Assets"].get_item(Key={"pk": "DIV#DLI", "sk": "HEALTH"})["Item"]
    assert health["not_reporting"] == ["DLI-004-UTS"]
    assert health["silent"] == ["DLI-002-UTS", "DLI-002-VIB", "DLI-003-UTS"]
