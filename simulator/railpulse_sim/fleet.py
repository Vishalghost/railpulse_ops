"""Deterministic fleet of divisions → lines → block sections (segments) → sensors.

Each line is a real route drawn station to station; a segment is the block section between
two consecutive stations. Station coordinates come from OpenStreetMap (© OpenStreetMap
contributors, ODbL). The same fleet is used by the simulator and by seed_assets.py, so
sensor IDs in telemetry always match rows in the Assets table.
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
    lang: str  # language gangmen are paged in
    # Climate + rail parameters. Illustrative values, not official IRPWM figures.
    ambient_mean_c: float
    seasonal_swing_c: float
    diurnal_swing_c: float
    solar_gain_c: float
    stress_free_temp_c: float


DIVISIONS: tuple[Division, ...] = (
    Division("DLI", "Delhi", "Northern Railway", "hi", 25.0, 9.0, 7.0, 16.0, 35.0),
    Division("BB", "Mumbai CSMT", "Central Railway", "mr", 27.5, 2.5, 4.0, 12.0, 38.0),
    Division("BCT", "Mumbai Central", "Western Railway", "mr", 27.5, 2.5, 4.0, 12.0, 38.0),
    Division("MAS", "Chennai", "Southern Railway", "ta", 29.0, 3.0, 5.0, 14.0, 40.0),
    Division("HWH", "Howrah", "Eastern Railway", "bn", 26.5, 6.0, 6.0, 14.0, 37.0),
    Division("SC", "Secunderabad", "South Central Railway", "te", 27.0, 5.0, 7.0, 15.0, 38.0),
    Division("SBC", "Bengaluru", "South Western Railway", "kn", 24.0, 3.0, 6.0, 13.0, 35.0),
    Division("LKO", "Lucknow", "Northern Railway", "hi", 25.5, 9.0, 7.0, 16.0, 35.0),
)
_BY_CODE = {d.code: d for d in DIVISIONS}


@dataclass(frozen=True)
class Line:
    division_code: str
    code: str
    name: str
    stations: tuple[tuple[str, float, float], ...]  # (name, lat, lon), in running order

    @property
    def division(self) -> Division:
        return _BY_CODE[self.division_code]


LINES: tuple[Line, ...] = (
    Line("DLI", "GT", "Delhi–Panipat main line", (
        ("Delhi Junction", 28.66093, 77.22765),
        ("Subzi Mandi", 28.66851, 77.20049),
        ("Adarsh Nagar", 28.71658, 77.17042),
        ("Badli", 28.74654, 77.13746),
        ("Narela", 28.84648, 77.08566),
        ("Sonipat", 28.98984, 77.01712),
        ("Ganaur", 29.13153, 77.01109),
        ("Samalkha", 29.24151, 77.00329),
        ("Panipat Junction", 29.39033, 76.96408),
    )),
    Line("DLI", "PWL", "New Delhi–Palwal", (
        ("New Delhi", 28.64193, 77.22175),
        ("Hazrat Nizamuddin", 28.58896, 77.25352),
        ("Okhla", 28.55948, 77.26562),
        ("Tughlakabad", 28.50344, 77.29705),
        ("Faridabad", 28.41076, 77.30708),
        ("Ballabhgarh", 28.34026, 77.3122),
        ("Palwal", 28.15158, 77.3418),
    )),
    Line("DLI", "GZB", "Delhi–Ghaziabad", (
        ("Delhi Junction", 28.66093, 77.22765),
        ("Shahdara", 28.67343, 77.28989),
        ("Sahibabad", 28.67428, 77.36443),
        ("Ghaziabad", 28.65286, 77.42824),
    )),
    Line("BB", "ML", "Central main line", (
        ("CSMT", 18.94083, 72.83186),
        ("Byculla", 18.97662, 72.83279),
        ("Dadar", 19.01923, 72.84285),
        ("Kurla", 19.06528, 72.87938),
        ("Ghatkopar", 19.08569, 72.90837),
        ("Vikhroli", 19.11148, 72.92802),
        ("Bhandup", 19.14276, 72.93765),
        ("Mulund", 19.17214, 72.9567),
        ("Thane", 19.18648, 72.97577),
        ("Diva", 19.18876, 73.04142),
        ("Dombivli", 19.21805, 73.08614),
        ("Kalyan", 19.23582, 73.13081),
    )),
    Line("BB", "HB", "Harbour line", (
        ("CSMT", 18.94083, 72.83186),
        ("Sewri", 18.99878, 72.85442),
        ("Wadala Road", 19.0161, 72.8592),
        ("Kurla", 19.06528, 72.87938),
        ("Chembur", 19.06263, 72.90114),
        ("Mankhurd", 19.04821, 72.93171),
        ("Vashi", 19.06329, 72.99876),
        ("Nerul", 19.03358, 73.01816),
        ("CBD Belapur", 19.01901, 73.03913),
        ("Kharghar", 19.02584, 73.0593),
        ("Panvel", 18.98942, 73.12269),
    )),
    Line("BB", "TH", "Trans-Harbour line", (
        ("Thane", 19.18648, 72.97577),
        ("Airoli", 19.15852, 72.9994),
        ("Ghansoli", 19.11631, 73.00702),
        ("Kopar Khairane", 19.1036, 73.0117),
        ("Turbhe", 19.07615, 73.0177),
        ("Sanpada", 19.06599, 73.00953),
        ("Vashi", 19.06329, 72.99876),
    )),
    Line("BCT", "WR", "Western line", (
        ("Churchgate", 18.93548, 72.82717),
        ("Mumbai Central", 18.96959, 72.81932),
        ("Dadar", 19.01738, 72.84303),
        ("Bandra", 19.05498, 72.84022),
        ("Andheri", 19.1197, 72.84642),
        ("Goregaon", 19.16487, 72.84955),
        ("Malad", 19.18672, 72.84859),
        ("Borivali", 19.22907, 72.85736),
        ("Bhayandar", 19.31018, 72.8527),
        ("Vasai Road", 19.38267, 72.83202),
        ("Virar", 19.45531, 72.81182),
    )),
    Line("BCT", "VD", "Vasai Road–Diva", (
        ("Vasai Road", 19.38267, 72.83202),
        ("Juchandra", 19.36046, 72.87232),
        ("Kaman Road", 19.33666, 72.91908),
        ("Kharbao", 19.29832, 72.98447),
        ("Bhiwandi Road", 19.26887, 73.04631),
        ("Kopar", 19.21242, 73.07886),
        ("Diva", 19.18876, 73.04142),
    )),
    Line("MAS", "AJJ", "Chennai Central–Arakkonam", (
        ("Chennai Central", 13.08242, 80.27596),
        ("Basin Bridge", 13.10203, 80.27126),
        ("Perambur", 13.10772, 80.24441),
        ("Villivakkam", 13.11032, 80.20916),
        ("Ambattur", 13.11409, 80.15286),
        ("Avadi", 13.11827, 80.10154),
        ("Tiruvallur", 13.11619, 79.91216),
        ("Arakkonam", 13.08234, 79.66853),
    )),
    Line("MAS", "CGL", "Chennai Beach–Chengalpattu", (
        ("Chennai Beach", 13.09159, 80.29183),
        ("Chennai Egmore", 13.07775, 80.26126),
        ("Mambalam", 13.03807, 80.22778),
        ("Guindy", 13.00927, 80.21308),
        ("St. Thomas Mount", 12.99477, 80.19937),
        ("Tambaram", 12.92579, 80.11792),
        ("Guduvancheri", 12.84523, 80.05765),
        ("Chengalpattu", 12.69347, 79.98052),
    )),
    Line("MAS", "GPD", "Chennai Central–Gummidipoondi", (
        ("Chennai Central", 13.08242, 80.27596),
        ("Korukkupet", 13.11942, 80.27846),
        ("Tiruvottiyur", 13.15864, 80.29621),
        ("Ennore", 13.21747, 80.3216),
        ("Minjur", 13.28134, 80.26024),
        ("Ponneri", 13.33234, 80.19888),
        ("Gummidipoondi", 13.40982, 80.12369),
    )),
    Line("HWH", "BDC", "Howrah–Bandel", (
        ("Howrah", 22.58287, 88.34281),
        ("Liluah", 22.6209, 88.3394),
        ("Belur", 22.63573, 88.33982),
        ("Bally", 22.65233, 88.33854),
        ("Uttarpara", 22.66705, 88.34115),
        ("Konnagar", 22.70126, 88.34251),
        ("Serampore", 22.75458, 88.33831),
        ("Sheoraphuli", 22.7748, 88.32836),
        ("Chandannagar", 22.86707, 88.35408),
        ("Chinsurah", 22.89091, 88.37028),
        ("Bandel", 22.92367, 88.37829),
    )),
    Line("HWH", "TAK", "Sheoraphuli–Tarakeswar", (
        ("Sheoraphuli", 22.7748, 88.32836),
        ("Nalikul", 22.83047, 88.16682),
        ("Haripal", 22.83162, 88.11915),
        ("Kamarkundu", 22.82108, 88.20326),
        ("Tarakeswar", 22.8824, 88.0142),
    )),
    Line("SC", "KZJ", "Secunderabad–Kazipet", (
        ("Secunderabad", 17.43386, 78.50205),
        ("Moula Ali", 17.44074, 78.55067),
        ("Cherlapalli", 17.4586, 78.60661),
        ("Ghatkesar", 17.45286, 78.6783),
        ("Bhongir", 17.52398, 78.89836),
        ("Aler", 17.64563, 79.0489),
        ("Jangaon", 17.72403, 79.15371),
        ("Ghanpur", 17.84978, 79.37634),
        ("Kazipet", 17.97483, 79.51127),
    )),
    Line("SC", "LPI", "Secunderabad–Lingampalli", (
        ("Secunderabad", 17.43386, 78.50205),
        ("Begumpet", 17.4387, 78.4581),
        ("Sanathnagar", 17.46224, 78.4367),
        ("Borabanda", 17.45907, 78.40787),
        ("Hafeezpet", 17.4824, 78.36301),
        ("Chandanagar", 17.4873, 78.33221),
        ("Lingampalli", 17.48312, 78.31777),
    )),
    Line("SBC", "BWT", "Bengaluru–Bangarapet", (
        ("KSR Bengaluru", 12.97817, 77.56956),
        ("Bengaluru Cantonment", 12.99369, 77.598),
        ("Baiyappanahalli", 12.99076, 77.65236),
        ("Krishnarajapuram", 13.00055, 77.67546),
        ("Whitefield", 12.9964, 77.76142),
        ("Malur", 12.99285, 77.93874),
        ("Bangarapet", 12.98549, 78.17822),
    )),
    Line("SBC", "TK", "Yesvantpur–Tumakuru", (
        ("Yesvantpur", 13.02377, 77.55077),
        ("Chikkabanavara", 13.07468, 77.50564),
        ("Dobbaspet", 13.23284, 77.24892),
        ("Tumakuru", 13.3339, 77.10196),
    )),
    Line("SBC", "MYS", "Bengaluru–Channapatna", (
        ("KSR Bengaluru", 12.97817, 77.56956),
        ("Kengeri", 12.91766, 77.48376),
        ("Bidadi", 12.80062, 77.39017),
        ("Ramanagara", 12.72105, 77.28374),
        ("Channapatna", 12.65937, 77.20389),
    )),
    Line("LKO", "CNB", "Lucknow–Kanpur", (
        ("Lucknow Charbagh", 26.83116, 80.92436),
        ("Manak Nagar", 26.81068, 80.89094),
        ("Amausi", 26.77136, 80.8787),
        ("Harauni", 26.70634, 80.74788),
        ("Ajgain", 26.61736, 80.62135),
        ("Unnao Junction", 26.54924, 80.48744),
        ("Kanpur Central", 26.45386, 80.35124),
    )),
    Line("LKO", "BBK", "Lucknow–Barabanki", (
        ("Lucknow Charbagh", 26.83116, 80.92436),
        ("Badshahnagar", 26.8707, 80.96101),
        ("Malhaur", 26.85368, 81.0408),
        ("Safedabad", 26.89803, 81.11678),
        ("Barabanki Junction", 26.93405, 81.17875),
    )),
)

SWITCH_EVERY_N_SEGMENTS = 3  # point-machine monitor on the 1st, 4th, 7th … block of each line


@dataclass(frozen=True)
class Segment:
    segment_id: str
    division: Division
    line: Line
    index: int  # 1-based within the division (the number in the segment ID)
    seq: int    # 1-based within the line
    from_station: str
    to_station: str
    km_from: float
    km_to: float
    from_lat: float
    from_lon: float
    to_lat: float
    to_lon: float
    has_switch: bool

    @property
    def lat(self) -> float:
        return round((self.from_lat + self.to_lat) / 2, 6)

    @property
    def lon(self) -> float:
        return round((self.from_lon + self.to_lon) / 2, 6)


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


def _km(a: tuple[str, float, float], b: tuple[str, float, float]) -> float:
    """Great-circle distance; track runs a little longer than the chord, so add 5 %."""
    (_, lat1, lon1), (_, lat2, lon2) = a, b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h)) * 1.05


def build_fleet(division_codes: list[str] | None = None) -> Fleet:
    if division_codes:
        unknown = set(division_codes) - _BY_CODE.keys()
        if unknown:
            raise ValueError(f"unknown division in {division_codes}; known: {', '.join(_BY_CODE)}")

    segments: list[Segment] = []
    sensors: list[Sensor] = []
    for div in DIVISIONS:
        if division_codes is not None and div.code not in division_codes:
            continue
        index = 0
        for line in (ln for ln in LINES if ln.division_code == div.code):
            km = 0.0
            for seq, (a, b) in enumerate(zip(line.stations, line.stations[1:]), start=1):
                index += 1
                length = round(_km(a, b), 1)
                seg = Segment(
                    segment_id=f"SEG-{div.code}-{index:03d}",
                    division=div, line=line, index=index, seq=seq,
                    from_station=a[0], to_station=b[0],
                    km_from=round(km, 1), km_to=round(km + length, 1),
                    from_lat=a[1], from_lon=a[2], to_lat=b[1], to_lon=b[2],
                    has_switch=seq % SWITCH_EVERY_N_SEGMENTS == 1,
                )
                km += length
                segments.append(seg)
                types = [SensorType.VIBRATION, SensorType.TEMPERATURE, SensorType.ULTRASONIC]
                if seg.has_switch:
                    types.append(SensorType.SWITCH)
                sensors.extend(Sensor(f"{div.code}-{index:03d}-{t}", t, seg) for t in types)

    return Fleet(tuple(segments), tuple(sensors))
