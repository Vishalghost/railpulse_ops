"""python -m railpulse_sim --help"""

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .faults import AFFECTED, Fault, FaultType, apply_fault, parse_fault
from .fleet import build_fleet
from .models import ReadingGenerator
from .publisher import MqttPublisher, Publisher, StdoutPublisher, shards_for_rate

DEFAULT_CERT_DIR = Path(__file__).resolve().parent.parent / "certs"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="railpulse_sim", description="Stream simulated trackside telemetry.")
    p.add_argument("--dry-run", action="store_true", help="print JSON lines to stdout instead of publishing")
    p.add_argument("--divisions", default="DLI,MAS,BB", help="comma-separated division codes")
    p.add_argument("--limit", type=int, default=0, help="only simulate the first N sensors (0 = all)")
    p.add_argument("--interval", type=float, default=5.0, help="seconds between readings per sensor")
    p.add_argument("--duration", type=float, default=0, help="stop after N seconds (0 = run forever)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--fault", action="append", default=[], metavar="TYPE@SEGMENT[+SEC]",
                   help="inject a fault; repeatable (crack|buckling|switch|silent)")
    p.add_argument("--burst-at", type=float, help="start a peak-transit burst N seconds into the run")
    p.add_argument("--burst-for", type=float, default=120, help="burst length in seconds")
    p.add_argument("--burst-factor", type=float, default=5, help="rate multiplier during the burst")
    p.add_argument("--endpoint", help="IoT Data-ATS endpoint (default: $RAILPULSE_IOT_ENDPOINT or certs/endpoint.txt)")
    p.add_argument("--certs", type=Path, default=DEFAULT_CERT_DIR)
    return p.parse_args(argv)


def resolve_endpoint(args: argparse.Namespace) -> str:
    if args.endpoint:
        return args.endpoint
    if env := os.environ.get("RAILPULSE_IOT_ENDPOINT"):
        return env
    saved = args.certs / "endpoint.txt"
    if saved.exists():
        return saved.read_text().strip()
    sys.exit("No IoT endpoint: run `python provision.py` first, or pass --endpoint.")


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    fleet = build_fleet(args.divisions.split(","))
    sensors = fleet.sensors[: args.limit] if args.limit else fleet.sensors

    faults: list[Fault] = [parse_fault(spec) for spec in args.fault]
    for f in faults:
        fleet.segment(f.segment_id)  # KeyError early on a typo, not mid-run

    def burst_active(t: float) -> bool:
        return args.burst_at is not None and args.burst_at <= t < args.burst_at + args.burst_for

    peak_rate = len(sensors) / args.interval * (args.burst_factor if args.burst_at is not None else 1)
    publisher: Publisher
    if args.dry_run:
        publisher = StdoutPublisher()
    else:
        shards = shards_for_rate(peak_rate)
        publisher = MqttPublisher(resolve_endpoint(args), args.certs, shards)
        print(f"connected {shards} MQTT connection(s) for peak {peak_rate:.0f} msg/s", file=sys.stderr)

    gen = ReadingGenerator(sensors, seed=args.seed)
    print(f"simulating {len(sensors)} sensors, {len(faults)} fault(s)", file=sys.stderr)

    start = time.monotonic()
    next_tick = start
    sent = 0
    try:
        while True:
            elapsed = time.monotonic() - start
            if args.duration and elapsed >= args.duration:
                break
            when = datetime.now(timezone.utc)
            for sensor in sensors:
                active = [f for f in faults
                          if f.segment_id == sensor.segment.segment_id
                          and elapsed >= f.start_s
                          and sensor.type in AFFECTED[f.type]]
                if any(f.type is FaultType.SILENT for f in active):
                    continue
                metrics = gen.metrics(sensor, when)
                for f in active:
                    metrics = apply_fault(sensor.type, metrics, f.type, elapsed - f.start_s)
                publisher.publish(sensor.topic, gen.build_payload(sensor, metrics, when), key=sensor.sensor_id)
                sent += 1
            factor = args.burst_factor if burst_active(elapsed) else 1
            next_tick += args.interval / factor
            wake = min(next_tick, start + args.duration) if args.duration else next_tick
            time.sleep(max(0.0, wake - time.monotonic()))
    except KeyboardInterrupt:
        pass
    finally:
        publisher.close()
        print(f"sent {sent} messages in {time.monotonic() - start:.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
