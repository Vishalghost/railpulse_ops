"""Detection rules. Pure functions: no AWS calls, so every threshold is unit-testable.

Two layers per sensor type:
  1. physics / absolute limits  — fire from the first reading, no history needed
  2. per-sensor z-scores        — fire once the sensor's own baseline is warm

Only readings that raise no finding are fed back into the baseline ("learn"), so a slowly
growing defect can't drag the baseline along with it and hide itself.
"""

from dataclasses import dataclass, field
from enum import IntEnum

from .schema import TelemetryMessage
from .stats import SensorStats


class Severity(IntEnum):
    """Lower is worse, so min() picks the most urgent."""
    P1 = 1  # stop/caution order: page gangman now, escalate fast
    P2 = 2  # inspect this shift
    P3 = 3  # inspect within 24 h
    P4 = 4  # advisory / sensor housekeeping


# --- thresholds (illustrative; tune against field data) ---------------------------------
CRACK_P2_DROP_DB = 6.0      # back-wall echo loss vs. this sensor's baseline
CRACK_P1_DROP_DB = 12.0
FLAW_ECHO_DB = 30.0         # healthy noise floor ≈ 12 dB
IMPACT_KURTOSIS = 8.0       # healthy train pass ≈ 3.2; a wheel striking a crack gap is impulsive
BUCKLING_P3_DELTA_C = 20.0  # rail temp above stress-free temperature
BUCKLING_P2_DELTA_C = 25.0
COLD_STRESS_DELTA_C = -30.0 # rail temp below stress-free temperature (fracture-prone)
SWITCH_THROW_MS = 6000.0
SWITCH_CURRENT_A = 5.0
LOW_BATTERY_PCT = 15.0
Z_LIMIT = 4.0

MIN_STD = {
    "backwall_echo_db": 0.2,
    "kurtosis@train": 0.15,
    "rms_g@train": 0.05,
    "throw_time_ms": 50.0,
    "motor_current_a": 0.05,
}


@dataclass(frozen=True)
class SegmentInfo:
    segment_id: str
    division: str
    stress_free_temp_c: float | None = None
    km_from: float | None = None
    line_code: str | None = None
    line_name: str | None = None
    section: str | None = None  # "Thane – Diva"


@dataclass(frozen=True)
class Finding:
    code: str
    severity: Severity
    metric: str
    observed: float
    expected: float | None
    detail: str


@dataclass
class Evaluation:
    findings: list[Finding] = field(default_factory=list)
    learn: dict[str, float] = field(default_factory=dict)  # baseline key -> value

    def add(self, *args) -> None:
        self.findings.append(Finding(*args))


def _num(metrics: dict, name: str) -> float | None:
    v = metrics.get(name)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _z(stats: SensorStats, key: str, x: float) -> float | None:
    st = stats.get(key)
    return st.zscore(x, MIN_STD[key]) if st else None


def evaluate(msg: TelemetryMessage, stats: SensorStats, segment: SegmentInfo | None) -> Evaluation:
    ev = Evaluation()
    m = msg.metrics
    match msg.type:
        case "UTS":
            _ultrasonic(ev, m, stats)
        case "VIB":
            _vibration(ev, m, stats)
        case "TMP":
            _temperature(ev, m, segment)
        case "SWT":
            _switch(ev, m, stats)
        case "HB":
            _heartbeat(ev, m)

    if msg.battery_pct is not None and msg.battery_pct < LOW_BATTERY_PCT:
        ev.add("LOW_BATTERY", Severity.P4, "battery_pct", msg.battery_pct, LOW_BATTERY_PCT,
               f"sensor battery at {msg.battery_pct:.0f}%")
    return ev


def _ultrasonic(ev: Evaluation, m: dict, stats: SensorStats) -> None:
    if m.get("probe_coupling_ok") is False:
        # Lost coupling also drops the echo — don't misread it as a crack.
        ev.add("SENSOR_FAULT", Severity.P4, "probe_coupling_ok", 0, 1, "ultrasonic probe lost coupling")
        return

    crack = False
    backwall = _num(m, "backwall_echo_db")
    if backwall is not None:
        st = stats.get("backwall_echo_db")
        if st and st.warm:
            drop = st.mean - backwall
            if drop >= CRACK_P2_DROP_DB:
                sev = Severity.P1 if drop >= CRACK_P1_DROP_DB else Severity.P2
                ev.add("CRACK_SUSPECTED", sev, "backwall_echo_db", backwall, round(st.mean, 2),
                       f"back-wall echo {drop:.1f} dB below baseline")
                crack = True

    flaw = _num(m, "flaw_echo_db")
    if flaw is not None and flaw >= FLAW_ECHO_DB:
        ev.add("CRACK_SUSPECTED", Severity.P2, "flaw_echo_db", flaw, FLAW_ECHO_DB,
               f"flaw echo {flaw:.1f} dB above detection gate")
        crack = True

    if backwall is not None and not crack:
        ev.learn["backwall_echo_db"] = backwall


def _vibration(ev: Evaluation, m: dict, stats: SensorStats) -> None:
    if m.get("train_passing") is not True:
        return  # idle-track vibration is mostly noise; judge only under load

    kurt = _num(m, "kurtosis")
    if kurt is not None:
        z = _z(stats, "kurtosis@train", kurt)
        if kurt >= IMPACT_KURTOSIS:
            ev.add("IMPACT_ANOMALY", Severity.P2, "kurtosis", kurt, IMPACT_KURTOSIS,
                   f"impulsive vibration under load (kurtosis {kurt:.1f})")
        elif z is not None and z >= Z_LIMIT:
            ev.add("IMPACT_ANOMALY", Severity.P3, "kurtosis", kurt, round(stats["kurtosis@train"].mean, 2),
                   f"kurtosis {z:.1f}σ above this sensor's baseline")
        else:
            ev.learn["kurtosis@train"] = kurt

    rms = _num(m, "rms_g")
    if rms is not None:
        z = _z(stats, "rms_g@train", rms)
        if z is not None and z >= Z_LIMIT:
            ev.add("VIBRATION_ANOMALY", Severity.P3, "rms_g", rms, round(stats["rms_g@train"].mean, 3),
                   f"RMS vibration {z:.1f}σ above baseline")
        else:
            ev.learn["rms_g@train"] = rms


def _temperature(ev: Evaluation, m: dict, segment: SegmentInfo | None) -> None:
    rail = _num(m, "rail_temp_c")
    if rail is None or segment is None or segment.stress_free_temp_c is None:
        return
    sft = segment.stress_free_temp_c
    delta = rail - sft
    if delta >= BUCKLING_P3_DELTA_C:
        sev = Severity.P2 if delta >= BUCKLING_P2_DELTA_C else Severity.P3
        ev.add("BUCKLING_RISK", sev, "rail_temp_c", rail, sft,
               f"rail {delta:+.1f} °C vs stress-free temperature — lateral buckling risk")
    elif delta <= COLD_STRESS_DELTA_C:
        ev.add("COLD_STRESS", Severity.P4, "rail_temp_c", rail, sft,
               f"rail {delta:+.1f} °C vs stress-free temperature — fracture-prone window")


def _heartbeat(ev: Evaluation, m: dict) -> None:
    """A whole segment going quiet at once looks like a cut cable or theft; one quiet
    sensor is usually a flat battery or a fault — housekeeping, not an emergency."""
    silent_s = _num(m, "silent_s") or 0.0
    minutes = silent_s / 60
    if m.get("segment_silent") is True:
        ev.add("SEGMENT_SILENT", Severity.P2, "silent_s", silent_s, None,
               f"every sensor on the segment silent for {minutes:.0f} min — possible cable cut or theft")
    else:
        ev.add("SENSOR_SILENT", Severity.P4, "silent_s", silent_s, None,
               f"sensor silent for {minutes:.0f} min")


def _switch(ev: Evaluation, m: dict, stats: SensorStats) -> None:
    if m.get("detection_ok") is False:
        ev.add("SWITCH_FAILURE", Severity.P1, "detection_ok", 0, 1,
               "point detection lost — switch position unproven")

    for key, limit, label in (
        ("throw_time_ms", SWITCH_THROW_MS, "throw time"),
        ("motor_current_a", SWITCH_CURRENT_A, "motor current"),
    ):
        x = _num(m, key)
        if x is None:
            continue
        z = _z(stats, key, x)
        if x >= limit:
            ev.add("SWITCH_DEGRADED", Severity.P2, key, x, limit, f"{label} {x:g} over limit {limit:g}")
        elif z is not None and z >= Z_LIMIT:
            ev.add("SWITCH_DEGRADED", Severity.P3, key, x, round(stats[key].mean, 2),
                   f"{label} {z:.1f}σ above baseline")
        else:
            ev.learn[key] = x
