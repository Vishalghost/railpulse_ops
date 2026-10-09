from datetime import datetime, timezone

import pytest

from railpulse_sim.faults import FaultType, parse_fault
from railpulse_sim.fleet import SensorType, build_fleet
from railpulse_sim.models import IST, ReadingGenerator, is_peak
from railpulse_sim.publisher import shards_for_rate

NOON_IST_JAN = datetime(2026, 1, 15, 12, 0, tzinfo=IST)
PREDAWN_IST_JAN = datetime(2026, 1, 15, 4, 0, tzinfo=IST)


def test_fleet_is_deterministic_and_ids_are_unique():
    a, b = build_fleet(), build_fleet()
    assert [s.sensor_id for s in a.sensors] == [s.sensor_id for s in b.sensors]
    ids = [s.sensor_id for s in a.sensors]
    assert len(ids) == len(set(ids))
    # 3 sensors per block section, plus a point-machine monitor on every 3rd block of a line
    assert len(ids) == 3 * len(a.segments) + sum(seg.has_switch for seg in a.segments)


def test_lines_are_continuous_station_to_station():
    fleet = build_fleet()
    assert {s.division.code for s in fleet.segments} == {"DLI", "BB", "BCT", "MAS", "HWH", "SC", "SBC", "LKO"}
    by_line: dict[str, list] = {}
    for seg in fleet.segments:
        by_line.setdefault(seg.line.code + seg.division.code, []).append(seg)
        assert 6 < seg.lat < 36 and 68 < seg.lon < 98  # inside India
        assert 0.3 < seg.km_to - seg.km_from < 40      # a plausible block section
    for segs in by_line.values():
        for prev, nxt in zip(segs, segs[1:]):
            assert prev.to_station == nxt.from_station
            assert prev.km_to == nxt.km_from
    main = [s for s in fleet.segments if s.line.code == "ML"]
    assert (main[0].from_station, main[-1].to_station) == ("CSMT", "Kalyan")
    assert 50 < main[-1].km_to < 57  # CSMT–Kalyan is ~54 km


def test_topic_carries_division_and_sensor_id():
    s = build_fleet(["DLI"]).sensors[0]
    assert s.topic == f"railpulse/DLI/{s.sensor_id}/telemetry"


def test_unknown_division_rejected():
    with pytest.raises(ValueError):
        build_fleet(["XYZ"])


def test_payload_omits_identity_fields_set_by_iot_rule():
    fleet = build_fleet(["MAS"])
    gen = ReadingGenerator(fleet.sensors)
    s = fleet.sensors[0]
    payload = gen.build_payload(s, gen.metrics(s, NOON_IST_JAN), NOON_IST_JAN)
    assert {"v", "segment_id", "type", "ts", "seq", "battery_pct", "metrics"} == payload.keys()
    assert payload["seq"] == 1


def test_same_seed_gives_same_readings():
    sensors = build_fleet(["BB"]).sensors[:10]
    a, b = ReadingGenerator(sensors, seed=7), ReadingGenerator(sensors, seed=7)
    assert [a.metrics(s, NOON_IST_JAN) for s in sensors] == [b.metrics(s, NOON_IST_JAN) for s in sensors]


def test_rail_runs_hotter_at_noon_than_predawn():
    fleet = build_fleet(["DLI"])
    tmp = next(s for s in fleet.sensors if s.type is SensorType.TEMPERATURE)
    gen = ReadingGenerator([tmp])
    noon = gen.metrics(tmp, NOON_IST_JAN)["rail_temp_c"]
    predawn = gen.metrics(tmp, PREDAWN_IST_JAN)["rail_temp_c"]
    assert noon - predawn > 10
    # Delhi winter pre-dawn rail is far below stress-free temp: the fracture-risk window.
    assert predawn < tmp.division.stress_free_temp_c - 20


def test_peak_hours():
    assert is_peak(datetime(2026, 1, 15, 8, 30, tzinfo=IST))
    assert not is_peak(datetime(2026, 1, 15, 13, 0, tzinfo=IST))
    assert is_peak(datetime(2026, 1, 15, 12, 30, tzinfo=timezone.utc))  # 18:00 IST


@pytest.mark.parametrize("spec,expected", [
    ("crack@SEG-DLI-012", (FaultType.CRACK, "SEG-DLI-012", 0.0)),
    ("silent@SEG-BB-003+45", (FaultType.SILENT, "SEG-BB-003", 45.0)),
])
def test_parse_fault(spec, expected):
    f = parse_fault(spec)
    assert (f.type, f.segment_id, f.start_s) == expected


@pytest.mark.parametrize("spec", ["crack", "melt@SEG-DLI-001", "crack@DLI-001"])
def test_parse_fault_rejects_bad_specs(spec):
    with pytest.raises(ValueError):
        parse_fault(spec)


def test_shards_respect_iot_connection_limit():
    assert shards_for_rate(10) == 1
    assert shards_for_rate(80) == 1
    assert shards_for_rate(213) == 3
