"""Deterministic fleet of divisions → track segments → sensors.

The same fleet is used by the simulator and by seed_assets.py, so sensor IDs in telemetry
always match rows in the Assets table.
"""

import math
from dataclasses import dataclass
from enum import StrEnum


class SensorType(StrEnum):
    VIBRATION = "VIB"    # rail-mounted accelerometer, pre-aggregated over 1 s windows
    TEMPERATURE = "TMP"  # rail-web thermistor + ambient probe
    ULTRASONIC = "UTS"   # fixed ultrasonic transducer (back-wall / flaw echo)
    SWITCH = "SWT"       # point-machine monitor (throw time, motor current)


@dataclass(frozen=True)
class Division:
    code: str
    name: str
    zone: str
    origin: tuple[float, float]  # lat, lon of km 0
    bearing_deg: float           # rough direction the modelled line runs
    km_start: float
    # Climate + rail parameters. Illustrative values, not official IRPWM figures.
    ambient_mean_c: float
    seasonal_swing_c: float
    diurnal_swing_c: float
    solar_gain_c: float
    stress_free_temp_c: float


DIVISIONS: tuple[Division, ...] = (
    Division("DLI", "Delhi", "Northern Railway", (28.6430, 77.2194), 340.0, 0.0,
             ambient_mean_c=25.0, seasonal_swing_c=9.0, diurnal_swing_c=7.0, solar_gain_c=16.0,
             stress_free_temp_c=35.0),
    Division("MAS", "Chennai", "Southern Railway", (13.0827, 80.2750), 225.0, 0.0,
             ambient_mean_c=29.0, seasonal_swing_c=3.0, diurnal_swing_c=5.0, solar_gain_c=14.0,
             stress_free_temp_c=40.0),
    Division("BB", "Mumbai", "Central Railway", (18.9398, 72.8355), 30.0, 0.0,
             ambient_mean_c=27.5, seasonal_swing_c=2.5, diurnal_swing_c=4.0, solar_gain_c=12.0,
             stress_free_temp_c=38.0),
)

SEGMENTS_PER_DIVISION = 22
SEGMENT_LENGTH_KM = 1.0
SWITCH_EVERY_N_SEGMENTS = 5


@dataclass(frozen=True)
class Segment:
    segment_id: str
    division: Division
    index: int
    km_from: float
    km_to: float
    lat: float
    lon: float
    has_switch: bool


@dataclass(frozen=True)
class Sensor:
    sensor_id: str
    type: SensorType
    segment: Segment

    @property
    def division(self) -> Division:
        return self.segment.division

    @property
    def topic(self) -> str:
        return f"railpulse/{self.division.code}/{self.sensor_id}/telemetry"


@dataclass(frozen=True)
class Fleet:
    segments: tuple[Segment, ...]
    sensors: tuple[Sensor, ...]

    def segment(self, segment_id: str) -> Segment:
        for seg in self.segments:
            if seg.segment_id == segment_id:
                return seg
        raise KeyError(segment_id)


def _offset(origin: tuple[float, float], bearing_deg: float, km: float) -> tuple[float, float]:
    # Equirectangular approximation — accurate to metres over a 22 km stretch.
    lat0, lon0 = origin
    b = math.radians(bearing_deg)
    dlat = km * math.cos(b) / 111.32
    dlon = km * math.sin(b) / (111.32 * math.cos(math.radians(lat0)))
    return round(lat0 + dlat, 6), round(lon0 + dlon, 6)


def build_fleet(division_codes: list[str] | None = None) -> Fleet:
    divisions = [d for d in DIVISIONS if division_codes is None or d.code in division_codes]
    if division_codes and len(divisions) != len(set(division_codes)):
        known = ", ".join(d.code for d in DIVISIONS)
        raise ValueError(f"unknown division in {division_codes}; known: {known}")

    segments: list[Segment] = []
    sensors: list[Sensor] = []
    for div in divisions:
        for i in range(1, SEGMENTS_PER_DIVISION + 1):
            km_from = div.km_start + (i - 1) * SEGMENT_LENGTH_KM
            lat, lon = _offset(div.origin, div.bearing_deg, km_from + SEGMENT_LENGTH_KM / 2)
            seg = Segment(
                segment_id=f"SEG-{div.code}-{i:03d}",
                division=div,
                index=i,
                km_from=km_from,
                km_to=km_from + SEGMENT_LENGTH_KM,
                lat=lat,
                lon=lon,
                has_switch=i % SWITCH_EVERY_N_SEGMENTS == 0,
            )
            segments.append(seg)
            types = [SensorType.VIBRATION, SensorType.TEMPERATURE, SensorType.ULTRASONIC]
            if seg.has_switch:
                types.append(SensorType.SWITCH)
            sensors.extend(Sensor(f"{div.code}-{i:03d}-{t}", t, seg) for t in types)

    return Fleet(tuple(segments), tuple(sensors))
