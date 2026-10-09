"""Load the simulated fleet into the Assets table so the worker and dashboard can resolve
segment → division, line, stations, km marker, location and stress-free temperature.

    python seed_assets.py [--stack RailPulse-dev-Core] [--region ap-south-1] [--demo-phone +91…]

Re-running is safe: rows for segments, sensors or contacts that are no longer in the fleet
are deleted, so the heartbeat doesn't keep reporting sensors that were removed.
"""

import argparse
from datetime import datetime, timezone
from decimal import Decimal

import boto3

from railpulse_sim.aws import DEFAULT_REGION, DEFAULT_STACK, stack_outputs
from railpulse_sim.fleet import DIVISIONS, LINES, build_fleet

# One gangman per line (their beat), then the division's Section Engineer and Divisional
# Engineer (alerting/railpulse_alerting/policy.py decides the order). Gangmen get the
# regional language, officers English. Placeholder numbers are deliberately unroutable;
# use --demo-phone to receive the pages yourself.
GANGMEN = {
    "DLI": ["Ramesh", "Suresh", "Mahesh"], "BB": ["Ganesh", "Santosh", "Prakash"],
    "BCT": ["Vijay", "Dilip"], "MAS": ["Murugan", "Selvam", "Karthik"], "HWH": ["Subrata", "Tapan"],
    "SC": ["Srinivas", "Ravi"], "SBC": ["Manjunath", "Basavaraj", "Nagaraj"], "LKO": ["Rajesh", "Anil"],
}


def dec(x: float) -> Decimal:
    return Decimal(str(x))  # DynamoDB rejects Python floats


def contacts(demo_phone: str | None) -> list[dict]:
    rows = []
    for div in DIVISIONS:
        lines = [ln for ln in LINES if ln.division_code == div.code]
        people = [(f"gm-{div.code.lower()}-{ln.code.lower()}", f"Gangman {name} ({ln.name})", "GANGMAN", div.lang,
                   ln.code) for ln, name in zip(lines, GANGMEN[div.code], strict=True)]
        people += [(f"sse-{div.code.lower()}", f"SSE/P.Way {div.name}", "SSE", div.lang, None),
                   (f"den-{div.code.lower()}", f"DEN {div.name}", "DEN", "en", None)]
        for i, (cid, name, role, lang, beat_line) in enumerate(people, start=1):
            item = {"pk": f"DIV#{div.code}", "sk": f"CONTACT#{role}#{cid}", "contact_id": cid, "name": name,
                    "role": role, "lang": lang, "phone": demo_phone or f"+910000000{i:03d}"}
            if beat_line:
                item["beat_line"] = beat_line
            rows.append(item)
    return rows


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

    items: list[dict] = []
    for seg in fleet.segments:
        line = {"line_code": seg.line.code, "line_name": seg.line.name, "line_seq": seg.seq,
                "from_station": seg.from_station, "to_station": seg.to_station,
                "km_from": dec(seg.km_from), "km_to": dec(seg.km_to), "lat": dec(seg.lat), "lon": dec(seg.lon),
                "from_lat": dec(seg.from_lat), "from_lon": dec(seg.from_lon),
                "to_lat": dec(seg.to_lat), "to_lon": dec(seg.to_lon), "has_switch": seg.has_switch}
        items.append({"pk": f"SEG#{seg.segment_id}", "sk": "META", "segment_id": seg.segment_id,
                      "division": seg.division.code, "division_name": seg.division.name, "zone": seg.division.zone,
                      "stress_free_temp_c": dec(seg.division.stress_free_temp_c), **line})
        # Division index: lets the dashboard map load a whole division in one query.
        items.append({"pk": f"DIV#{seg.division.code}", "sk": f"SEG#{seg.segment_id}",
                      "segment_id": seg.segment_id, **line})
    for s in fleet.sensors:
        items.append({"pk": f"SEG#{s.segment.segment_id}", "sk": f"SENSOR#{s.sensor_id}",
                      "sensor_id": s.sensor_id, "type": str(s.type), "division": s.division.code})
    people = contacts(args.demo_phone)
    items += people

    wanted = {(i["pk"], i["sk"]) for i in items}
    stale = []
    scan = {"ProjectionExpression": "pk, sk"}
    while True:
        page = table.scan(**scan)
        stale += [k for k in ((i["pk"], i["sk"]) for i in page["Items"])
                  if k not in wanted and (k[1] == "META" or k[1].startswith(("SEG#", "SENSOR#", "CONTACT#")))]
        if "LastEvaluatedKey" not in page:
            break
        scan["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    with table.batch_writer() as batch:
        for pk, sk in stale:
            batch.delete_item(Key={"pk": pk, "sk": sk})
        for item in items:
            batch.put_item(Item={**item, "seeded_at": now})

    print(f"seeded {len(fleet.segments)} segments on {len(LINES)} lines in {len(DIVISIONS)} divisions, "
          f"{len(fleet.sensors)} sensors and {len(people)} contacts into {table_name}; removed {len(stale)} stale rows")


if __name__ == "__main__":
    main()
