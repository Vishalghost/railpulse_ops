"""Fault injection: how each failure mode distorts healthy readings.

Spec syntax on the CLI:  TYPE@SEGMENT[+START_SECONDS]
    crack@SEG-DLI-012        start immediately
    buckling@SEG-MAS-004+60  start 60 s into the run
"""

import re
from dataclasses import dataclass
from enum import StrEnum

from .fleet import SensorType


class FaultType(StrEnum):
    CRACK = "crack"        # internal rail flaw / developing fracture
    BUCKLING = "buckling"  # thermal over-stress, lateral misalignment
    SWITCH = "switch"      # point machine degrading (obstruction, wear, low voltage)
    SILENT = "silent"      # cable cut / theft / dead battery: sensors stop reporting


# Which sensors on the faulty segment see the fault.
AFFECTED: dict[FaultType, frozenset[SensorType]] = {
    FaultType.CRACK: frozenset({SensorType.ULTRASONIC, SensorType.VIBRATION}),
    FaultType.BUCKLING: frozenset({SensorType.TEMPERATURE, SensorType.VIBRATION}),
    FaultType.SWITCH: frozenset({SensorType.SWITCH}),
    FaultType.SILENT: frozenset(SensorType),
}


@dataclass(frozen=True)
class Fault:
    type: FaultType
    segment_id: str
    start_s: float = 0.0


_SPEC = re.compile(r"^(?P<type>[a-z]+)@(?P<segment>SEG-[A-Z]+-\d{3})(?:\+(?P<start>\d+(?:\.\d+)?))?$")


def parse_fault(spec: str) -> Fault:
    m = _SPEC.match(spec.strip())
    if not m:
        raise ValueError(f"bad fault spec {spec!r}; expected TYPE@SEG-XXX-NNN[+SECONDS]")
    try:
        ftype = FaultType(m["type"])
    except ValueError:
        raise ValueError(f"unknown fault type {m['type']!r}; choose from {[f.value for f in FaultType]}") from None
    return Fault(ftype, m["segment"], float(m["start"] or 0))


def apply_fault(sensor_type: SensorType, metrics: dict, fault: FaultType, elapsed_s: float) -> dict:
    """Return a copy of `metrics` distorted the way `fault` would distort this sensor.

    Called once per reading for every active fault on the sensor's segment (SILENT is handled
    by the caller — it never reaches here). `elapsed_s` is seconds since the fault started,
    so you can make damage progressive, e.g. a crack that grows over a few minutes.

    Healthy baselines (see models.py) for reference:
      VIB  rms_g 0.5–1.2 with a train passing, kurtosis ≈ 3.2, peak_g ≈ 3–4.5× rms
      TMP  rail_temp_c = ambient + solar gain; stress-free temp 35–40 °C
      UTS  backwall_echo_db 74–82 (±0.4), flaw_echo_db ≈ 12 (noise floor)
      SWT  throw_time_ms ≈ 4200 ± 150, motor_current_a ≈ 3.4 ± 0.15

    The Phase 2 detector will key off these deviations, so make them physically plausible
    rather than cartoonish — they define what "detectable" means in your demo.
    """
    # TODO(you): implement CRACK, BUCKLING and SWITCH signatures (5–10 lines).
    raise NotImplementedError(f"fault signature for {fault} on {sensor_type} not implemented yet")
