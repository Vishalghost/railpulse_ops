from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TelemetryMessage(BaseModel):
    """One SQS message as produced by the IoT Rule.

    The simulator payload (simulator/railpulse_sim/models.py) plus `division`, `sensor_id`
    and `ingest_ts`, which the rule derives from the MQTT topic.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    v: Literal[1]
    division: str = Field(pattern=r"^[A-Z]{2,4}$")
    sensor_id: str = Field(min_length=1, max_length=64)
    segment_id: str = Field(pattern=r"^SEG-[A-Z]{2,4}-\d{3}$")
    # HB = synthetic heartbeat-miss message from the alerting stack's heartbeat checker.
    type: Literal["VIB", "TMP", "UTS", "SWT", "HB"]
    ts: int = Field(gt=0, description="sensor timestamp, epoch ms")
    seq: int = Field(ge=0)
    battery_pct: float | None = None
    metrics: dict[str, bool | float]
    ingest_ts: int | None = None
