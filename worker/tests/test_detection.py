import pytest

from app.detection import SegmentInfo, Severity, evaluate
from app.schema import TelemetryMessage
from app.stats import WARMUP_SAMPLES, RunningStat

SEG = SegmentInfo("SEG-DLI-001", "DLI", stress_free_temp_c=35.0)


def msg(type_: str, metrics: dict, battery: float = 80.0) -> TelemetryMessage:
    return TelemetryMessage(v=1, division="DLI", sensor_id=f"DLI-001-{type_}", segment_id="SEG-DLI-001",
                            type=type_, ts=1_790_000_000_000, seq=1, battery_pct=battery, metrics=metrics)


def warm(mean: float, spread: float) -> RunningStat:
    st = RunningStat()
    for i in range(WARMUP_SAMPLES):
        st.update(mean + (spread if i % 2 else -spread))
    return st


def codes(ev):
    return [(f.code, f.severity) for f in ev.findings]


def test_running_stat_matches_population_variance_during_warmup():
    st = RunningStat()
    for x in (2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0):
        st.update(x)
    assert st.mean == pytest.approx(5.0)
    assert st.var == pytest.approx(4.0)


def test_healthy_ultrasonic_is_quiet_and_learned():
    ev = evaluate(msg("UTS", {"backwall_echo_db": 78.1, "flaw_echo_db": 12.0, "probe_coupling_ok": True}),
                  {"backwall_echo_db": warm(78.0, 0.4)}, SEG)
    assert ev.findings == []
    assert ev.learn == {"backwall_echo_db": 78.1}


@pytest.mark.parametrize("backwall,expected", [(71.0, Severity.P2), (65.0, Severity.P1)])
def test_backwall_drop_flags_crack(backwall, expected):
    ev = evaluate(msg("UTS", {"backwall_echo_db": backwall, "flaw_echo_db": 12.0, "probe_coupling_ok": True}),
                  {"backwall_echo_db": warm(78.0, 0.4)}, SEG)
    assert codes(ev) == [("CRACK_SUSPECTED", expected)]
    assert "backwall_echo_db" not in ev.learn  # the defect must not become the new normal


def test_crack_drop_ignored_until_baseline_is_warm():
    cold = RunningStat()
    cold.update(78.0)
    ev = evaluate(msg("UTS", {"backwall_echo_db": 60.0, "flaw_echo_db": 12.0}), {"backwall_echo_db": cold}, SEG)
    assert ev.findings == []


def test_lost_coupling_is_a_sensor_fault_not_a_crack():
    ev = evaluate(msg("UTS", {"backwall_echo_db": 40.0, "flaw_echo_db": 12.0, "probe_coupling_ok": False}),
                  {"backwall_echo_db": warm(78.0, 0.4)}, SEG)
    assert codes(ev) == [("SENSOR_FAULT", Severity.P4)]


def test_vibration_only_judged_under_load():
    idle = evaluate(msg("VIB", {"train_passing": False, "rms_g": 0.02, "kurtosis": 12.0}), {}, SEG)
    assert idle.findings == [] and idle.learn == {}
    loaded = evaluate(msg("VIB", {"train_passing": True, "rms_g": 0.8, "kurtosis": 9.5}), {}, SEG)
    assert codes(loaded) == [("IMPACT_ANOMALY", Severity.P2)]


def test_vibration_zscore_after_warmup():
    stats = {"kurtosis@train": warm(3.2, 0.3), "rms_g@train": warm(0.8, 0.2)}
    ev = evaluate(msg("VIB", {"train_passing": True, "rms_g": 0.85, "kurtosis": 5.0}), stats, SEG)
    assert codes(ev) == [("IMPACT_ANOMALY", Severity.P3)]
    assert ev.learn == {"rms_g@train": 0.85}


@pytest.mark.parametrize("rail,expected", [
    (50.0, []),
    (56.0, [("BUCKLING_RISK", Severity.P3)]),
    (61.0, [("BUCKLING_RISK", Severity.P2)]),
    (4.0, [("COLD_STRESS", Severity.P4)]),
])
def test_rail_temperature_vs_stress_free(rail, expected):
    assert codes(evaluate(msg("TMP", {"rail_temp_c": rail, "ambient_c": 20.0}), {}, SEG)) == expected


def test_temperature_skipped_without_asset_metadata():
    assert evaluate(msg("TMP", {"rail_temp_c": 70.0}), {}, None).findings == []


def test_switch_rules():
    ev = evaluate(msg("SWT", {"throw_time_ms": 6500.0, "motor_current_a": 5.4, "detection_ok": False}), {}, SEG)
    assert sorted(codes(ev)) == [("SWITCH_DEGRADED", Severity.P2), ("SWITCH_DEGRADED", Severity.P2),
                                 ("SWITCH_FAILURE", Severity.P1)]


def test_low_battery_advisory():
    ev = evaluate(msg("TMP", {"rail_temp_c": 40.0}, battery=9.0), {}, SEG)
    assert codes(ev) == [("LOW_BATTERY", Severity.P4)]
