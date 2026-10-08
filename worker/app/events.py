import json
from decimal import Decimal
from typing import Protocol

SOURCE = "railpulse.detection"


def _json_default(o):
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    if isinstance(o, set):
        return sorted(o)
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")


class Publisher(Protocol):
    def publish(self, detail_type: str, incident: dict) -> None: ...


class EventBridgePublisher:
    """Events are at-least-once (see IncidentRepo.pending_event): Phase 3 consumers must
    dedupe on (incident_id, severity)."""

    def __init__(self, client, bus_name: str) -> None:
        self._client = client
        self._bus = bus_name

    def publish(self, detail_type: str, incident: dict) -> None:
        detail = {k: v for k, v in incident.items() if k not in ("pk", "sk", "gsi1pk", "gsi1sk", "pending_event")}
        resp = self._client.put_events(Entries=[{
            "Source": SOURCE,
            "DetailType": detail_type,
            "EventBusName": self._bus,
            "Detail": json.dumps(detail, default=_json_default),
        }])
        if resp.get("FailedEntryCount"):
            raise RuntimeError(f"put_events failed: {resp['Entries'][0].get('ErrorMessage')}")
