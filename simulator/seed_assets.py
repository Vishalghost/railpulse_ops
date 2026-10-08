"""Load the simulated fleet into the Assets table so the worker and dashboard can resolve
segment → division, km marker, location and stress-free temperature.

    python seed_assets.py [--stack RailPulse-dev-Core] [--region ap-south-1]
"""

import argparse
from datetime import datetime, timezone
from decimal import Decimal

import boto3

from railpulse_sim.aws import DEFAULT_REGION, DEFAULT_STACK, stack_outputs
from railpulse_sim.fleet import build_fleet


# Escalation contacts per division (alerting/railpulse_alerting/policy.py decides the order).
# Placeholder numbers are deliberately unroutable; use --demo-phone to receive the pages yourself.
# Languages: gangmen get the regional language, officers English.
CONTACTS = {
    "DLI": [("gm-dli-1", "Gangman Ramesh", "GANGMAN", "hi", (0, 11)),
            ("gm-dli-2", "Gangman Suresh", "GANGMAN", "hi", (11, 22)),
            ("sse-dli", "SSE/P.Way Delhi", "SSE", "hi", None),
            ("den-dli", "DEN Delhi", "DEN", "en", None)],
    "MAS": [("gm-mas-1", "Gangman Murugan", "GANGMAN", "ta", (0, 11)),
            ("gm-mas-2", "Gangman Selvam", "GANGMAN", "ta", (11, 22)),
            ("sse-mas", "SSE/P.Way Chennai", "SSE", "ta", None),
            ("den-mas", "DEN Chennai", "DEN", "en", None)],
    "BB": [("gm-bb-1", "Gangman Ganesh", "GANGMAN", "mr", (0, 11)),
           ("gm-bb-2", "Gangman Santosh", "GANGMAN", "mr", (11, 22)),
           ("sse-bb", "SSE/P.Way Mumbai", "SSE", "mr", None),
           ("den-bb", "DEN Mumbai", "DEN", "en", None)],
}


def dec(x: float) -> Decimal:
    return Decimal(str(x))  # DynamoDB rejects Python floats


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--stack", default=DEFAULT_STACK)
    p.add_argument("--region", default=DEFAULT_REGION)
    p.add_argument("--demo-phone", help="E.164 number (e.g. +9198XXXXXXXX) to receive every page")
    args = p.parse_args()

    table_name = stack_outputs(args.stack, args.region)["AssetsTableName"]
    table = boto3.resource("dynamodb", region_name=args.region).Table(table_name)
    fleet = build_fleet()
    now = datetime.now(timezone.utc).isoformat()

    with table.batch_writer() as batch:
        for seg in fleet.segments:
            batch.put_item(Item={
                "pk": f"SEG#{seg.segment_id}", "sk": "META",
                "segment_id": seg.segment_id,
                "division": seg.division.code, "division_name": seg.division.name, "zone": seg.division.zone,
                "km_from": dec(seg.km_from), "km_to": dec(seg.km_to),
                "lat": dec(seg.lat), "lon": dec(seg.lon),
                "stress_free_temp_c": dec(seg.division.stress_free_temp_c),
                "has_switch": seg.has_switch,
                "seeded_at": now,
            })
            # Division index: lets the dashboard map load a whole division in one query.
            batch.put_item(Item={
                "pk": f"DIV#{seg.division.code}", "sk": f"SEG#{seg.segment_id}",
                "segment_id": seg.segment_id, "km_from": dec(seg.km_from), "km_to": dec(seg.km_to),
                "lat": dec(seg.lat), "lon": dec(seg.lon), "has_switch": seg.has_switch,
            })
        for s in fleet.sensors:
            batch.put_item(Item={
                "pk": f"SEG#{s.segment.segment_id}", "sk": f"SENSOR#{s.sensor_id}",
                "sensor_id": s.sensor_id, "type": str(s.type), "division": s.division.code,
                "seeded_at": now,
            })
        n_contacts = 0
        for division, people in CONTACTS.items():
            for i, (cid, name, role, lang, beat) in enumerate(people, start=1):
                item = {
                    "pk": f"DIV#{division}", "sk": f"CONTACT#{role}#{cid}",
                    "contact_id": cid, "name": name, "role": role, "lang": lang,
                    "phone": args.demo_phone or f"+910000000{i:03d}",
                    "seeded_at": now,
                }
                if beat:
                    item["beat_km_from"], item["beat_km_to"] = dec(beat[0]), dec(beat[1])
                batch.put_item(Item=item)
                n_contacts += 1

    print(f"seeded {len(fleet.segments)} segments, {len(fleet.sensors)} sensors and "
          f"{n_contacts} contacts into {table_name}")


if __name__ == "__main__":
    main()
