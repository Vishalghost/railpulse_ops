from dataclasses import dataclass, field

import boto3

from .config import Settings
from .detection import Finding, evaluate
from .events import EventBridgePublisher, Publisher
from .schema import TelemetryMessage
from .stats import RunningStat
from .store import AssetRepo, BaselineRepo, IncidentChange, IncidentRepo, TelemetryRepo


@dataclass
class ProcessResult:
    findings: list[Finding] = field(default_factory=list)
    changes: list[IncidentChange] = field(default_factory=list)


class Processor:
    def __init__(self, telemetry: TelemetryRepo, baselines: BaselineRepo, assets: AssetRepo,
                 incidents: IncidentRepo, events: Publisher) -> None:
        self.telemetry = telemetry
        self.baselines = baselines
        self.assets = assets
        self.incidents = incidents
        self.events = events

    def process(self, msg: TelemetryMessage) -> ProcessResult:
        is_heartbeat = msg.type == "HB"  # synthetic "sensor went silent" message, not a reading
        if not is_heartbeat:
            self.telemetry.put(msg)

        stats = {} if is_heartbeat else self.baselines.load(msg.sensor_id)
        segment = self.assets.get(msg.segment_id)
        ev = evaluate(msg, stats, segment)

        if ev.learn and not is_heartbeat:
            for key, value in ev.learn.items():
                stats.setdefault(key, RunningStat()).update(value)
            self.baselines.save(msg.sensor_id, stats)

        # Several rules can emit the same code (e.g. throw time + motor current); one incident each.
        worst: dict[str, Finding] = {}
        for f in ev.findings:
            if f.code not in worst or f.severity < worst[f.code].severity:
                worst[f.code] = f

        result = ProcessResult(findings=ev.findings)
        for f in worst.values():
            change = self.incidents.record(f, msg, segment)
            if change is None:
                continue
            # A failure here raises → sqsd retries → pending_event is still set → we resend.
            if pending := change.item.get("pending_event"):
                self.events.publish(pending, change.item)
                self.incidents.clear_pending(change.item, pending)
            result.changes.append(change)
        return result


def build_processor(settings: Settings) -> Processor:
    ddb = boto3.resource("dynamodb", region_name=settings.region)
    return Processor(
        telemetry=TelemetryRepo(ddb.Table(settings.telemetry_table), settings.telemetry_ttl_days),
        baselines=BaselineRepo(ddb.Table(settings.baselines_table)),
        assets=AssetRepo(ddb.Table(settings.assets_table)),
        incidents=IncidentRepo(ddb.Table(settings.incidents_table)),
        events=EventBridgePublisher(boto3.client("events", region_name=settings.region), settings.event_bus),
    )
