"""Healthy-baseline reading models for each sensor type.

Faults are layered on top of these in faults.py, so detection logic can be tested against
a known-normal baseline plus a known injected deviation.
"""

import math
import random
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .fleet import Division, Sensor, SensorType

IST = timezone(timedelta(hours=5, minutes=30))
PEAK_HOURS_IST = ((7, 10), (17, 21))
TELEMETRY_SCHEMA_VERSION = 1


def is_peak(when: datetime) -> bool:
    hour = when.astimezone(IST).hour
    return any(start <= hour < end for start, end in PEAK_HOURS_IST)


def ambient_temp_c(division: Division, when: datetime) -> float:
    local = when.astimezone(IST)
    day_of_year = local.timetuple().tm_yday
    hour = local.hour + local.minute / 60
    seasonal = division.seasonal_swing_c * math.cos(2 * math.pi * (day_of_year - 140) / 365)  # peak ≈ late May
    diurnal = division.diurnal_swing_c * math.cos(2 * math.pi * (hour - 14.5) / 24)          # peak ≈ 14:30
    return division.ambient_mean_c + seasonal + diurnal


def rail_temp_c(division: Division, when: datetime, ambient: float) -> float:
    # Steel in direct sun runs far hotter than air; on clear nights it radiates below ambient.
    hour = when.astimezone(IST).hour + when.astimezone(IST).minute / 60
    if 6.5 <= hour <= 18.5:
        return ambient + division.solar_gain_c * math.sin(math.pi * (hour - 6.5) / 12)
    return ambient - 1.5


@dataclass
class _SensorState:
    rng: random.Random
    seq: int = 0
    battery_pct: float = 100.0
    baseline: dict[str, float] = field(default_factory=dict)


class ReadingGenerator:
    def __init__(self, sensors: tuple[Sensor, ...] | list[Sensor], seed: int = 42) -> None:
        self._state: dict[str, _SensorState] = {}
        for s in sensors:
            # crc32 rather than hash(): str hashes are randomised per process.
            rng = random.Random(seed ^ zlib.crc32(s.sensor_id.encode()))
            st = _SensorState(rng=rng, battery_pct=rng.uniform(70, 100))
            if s.type is SensorType.ULTRASONIC:
                st.baseline["backwall_echo_db"] = rng.uniform(74, 82)
            self._state[s.sensor_id] = st

    def metrics(self, sensor: Sensor, when: datetime) -> dict:
        st = self._state[sensor.sensor_id]
        rng = st.rng
        match sensor.type:
            case SensorType.VIBRATION:
                passing = rng.random() < (0.35 if is_peak(when) else 0.12)
                if passing:
                    rms = rng.uniform(0.5, 1.2)
                    m = {"train_passing": True, "rms_g": rms, "peak_g": rms * rng.uniform(3.0, 4.5),
                         "kurtosis": rng.gauss(3.2, 0.3), "dominant_hz": rng.gauss(32, 4)}
                else:
                    rms = abs(rng.gauss(0.02, 0.005))
                    m = {"train_passing": False, "rms_g": rms, "peak_g": rms * 3,
                         "kurtosis": rng.gauss(3.0, 0.2), "dominant_hz": abs(rng.gauss(5, 2))}
            case SensorType.TEMPERATURE:
                ambient = ambient_temp_c(sensor.division, when) + rng.gauss(0, 0.3)
                m = {"ambient_c": ambient,
                     "rail_temp_c": rail_temp_c(sensor.division, when, ambient) + rng.gauss(0, 0.4)}
            case SensorType.ULTRASONIC:
                m = {"backwall_echo_db": st.baseline["backwall_echo_db"] + rng.gauss(0, 0.4),
                     "flaw_echo_db": abs(rng.gauss(12, 2)),
                     "probe_coupling_ok": True}
            case SensorType.SWITCH:
                m = {"throw_time_ms": rng.gauss(4200, 150),
                     "motor_current_a": rng.gauss(3.4, 0.15),
                     "detection_ok": True}
        return {k: round(v, 3) if isinstance(v, float) else v for k, v in m.items()}

    def build_payload(self, sensor: Sensor, metrics: dict, when: datetime) -> dict:
        """Wire format. division/sensor_id are deliberately absent — the IoT Rule adds them from the topic."""
        st = self._state[sensor.sensor_id]
        st.seq += 1
        st.battery_pct = max(0.0, st.battery_pct - 0.0005)
        return {
            "v": TELEMETRY_SCHEMA_VERSION,
            "segment_id": sensor.segment.segment_id,
            "type": str(sensor.type),
            "ts": int(when.timestamp() * 1000),
            "seq": st.seq,
            "battery_pct": round(st.battery_pct, 2),
            "metrics": metrics,
        }
