import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    region: str
    telemetry_table: str
    incidents_table: str
    assets_table: str
    baselines_table: str
    event_bus: str
    telemetry_ttl_days: int = 30

    @classmethod
    def from_env(cls) -> "Settings":
        # Set as EB environment properties by infra/railpulse_infra/worker_stack.py.
        return cls(
            region=os.environ.get("RAILPULSE_REGION", "ap-south-1"),
            telemetry_table=os.environ["TELEMETRY_TABLE"],
            incidents_table=os.environ["INCIDENTS_TABLE"],
            assets_table=os.environ["ASSETS_TABLE"],
            baselines_table=os.environ["BASELINES_TABLE"],
            event_bus=os.environ["EVENT_BUS_NAME"],
            telemetry_ttl_days=int(os.environ.get("TELEMETRY_TTL_DAYS", "30")),
        )
