"""Glue table schemas for the S3 data lake — one source of truth for the CDK tables and the
SQL tests (infra/tests/test_analytics.py checks every query against these columns).

Both tables are JSON lines, gzipped, read with the OpenX JSON SerDe.
"""

DIVISIONS = ("DLI", "BB", "BCT", "MAS", "HWH", "SC", "SBC", "LKO")
PROJECTION_START = "2026-01-01"

# Union of every sensor type's metric keys (simulator/railpulse_sim/models.py). A test fails
# if the simulator grows a metric this struct doesn't declare.
METRIC_FIELDS: dict[str, str] = {
    "train_passing": "boolean",
    "rms_g": "double",
    "peak_g": "double",
    "kurtosis": "double",
    "dominant_hz": "double",
    "ambient_c": "double",
    "rail_temp_c": "double",
    "backwall_echo_db": "double",
    "flaw_echo_db": "double",
    "probe_coupling_ok": "boolean",
    "throw_time_ms": "double",
    "motor_current_a": "double",
    "detection_ok": "boolean",
}


def _struct(fields: dict[str, str]) -> str:
    return "struct<" + ",".join(f"{k}:{v}" for k, v in fields.items()) + ">"


# s3://<lake>/raw/division=<div>/dt=<yyyy-MM-dd>/ — written by the IoT rule's Firehose (ingest.py).
TELEMETRY_TABLE = "telemetry_raw"
TELEMETRY_COLUMNS: list[tuple[str, str]] = [
    ("v", "int"),
    ("sensor_id", "string"),
    ("segment_id", "string"),
    ("sensor_type", "string"),  # JSON "type" (renamed: clearer, and not a keyword anywhere)
    ("ts", "bigint"),           # sensor clock, epoch ms
    ("seq", "bigint"),
    ("battery_pct", "double"),
    ("ingest_ts", "bigint"),    # IoT Core receive time, epoch ms
    ("metrics", _struct(METRIC_FIELDS)),
]
TELEMETRY_PARTITIONS = [("division", "string"), ("dt", "string")]
TELEMETRY_MAPPINGS = {"mapping.sensor_type": "type"}

# s3://<lake>/events/dt=<yyyy-MM-dd>/ — every detection event from the EventBridge bus.
EVENTS_TABLE = "incident_events"
INCIDENT_FIELDS: dict[str, str] = {
    "incident_id": "string",
    "type": "string",
    "status": "string",
    "severity": "int",
    "segment_id": "string",
    "division": "string",
    "km_from": "double",
    "first_seen": "bigint",
    "last_seen": "bigint",
    "occurrences": "int",
    "sensor_ids": "array<string>",
    "escalated_at": "bigint",
}
EVENTS_COLUMNS: list[tuple[str, str]] = [
    ("id", "string"),
    ("detail_type", "string"),  # JSON "detail-type"
    ("source", "string"),
    ("time", "string"),
    ("detail", _struct(INCIDENT_FIELDS)),
]
EVENTS_PARTITIONS = [("dt", "string")]
EVENTS_MAPPINGS = {"mapping.detail_type": "detail-type"}

TABLES: dict[str, dict[str, str]] = {
    TELEMETRY_TABLE: dict(TELEMETRY_COLUMNS + TELEMETRY_PARTITIONS),
    EVENTS_TABLE: dict(EVENTS_COLUMNS + EVENTS_PARTITIONS),
}
