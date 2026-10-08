from railpulse_alerting.messages import Translator, compose, sms_body
from railpulse_alerting.policy import build_ladder

from .conftest import INCIDENT, FakeTranslate

CONTACTS = [
    {"contact_id": "gm-1", "role": "GANGMAN", "lang": "hi", "phone": "+910000000001", "beat_km_from": 0, "beat_km_to": 11},
    {"contact_id": "gm-2", "role": "GANGMAN", "lang": "hi", "phone": "+910000000002", "beat_km_from": 11, "beat_km_to": 22},
    {"contact_id": "sse-1", "role": "SSE", "lang": "en", "phone": "+910000000003"},
    {"contact_id": "den-1", "role": "DEN", "lang": "en", "phone": "+910000000004"},
]


def test_p1_ladder_pages_the_beat_gangman_then_sse_then_den():
    ladder = build_ladder(CONTACTS, 1, km=11.0)
    assert [r["role"] for r in ladder] == ["GANGMAN", "SSE", "DEN"]
    assert [c["contact_id"] for c in ladder[0]["contacts"]] == ["gm-2"]
    assert all(r["ack_timeout_s"] > 0 for r in ladder)


def test_uncovered_km_pages_every_gangman():
    ladder = build_ladder(CONTACTS, 1, km=50.0)
    assert [c["contact_id"] for c in ladder[0]["contacts"]] == ["gm-1", "gm-2"]


def test_p3_is_inform_only_and_p4_pages_nobody():
    assert build_ladder(CONTACTS, 3, km=5.0) == [
        {"role": "SSE", "ack_timeout_s": 0,
         "contacts": [{"contact_id": "sse-1", "name": "sse-1", "phone": "+910000000003", "lang": "en"}]}]
    assert build_ladder(CONTACTS, 4, km=5.0) == []


def test_demo_override_shortens_waiting_rungs_only():
    assert {r["ack_timeout_s"] for r in build_ladder(CONTACTS, 1, 5.0, timeout_override_s=60)} == {60}
    assert build_ladder(CONTACTS, 3, 5.0, timeout_override_s=60)[0]["ack_timeout_s"] == 0


def test_missing_roles_are_skipped_not_fatal():
    assert [r["role"] for r in build_ladder(CONTACTS[2:3], 1, 5.0)] == ["SSE"]


def test_message_wording_and_sms_tail_kept_out_of_translation():
    core = compose(INCIDENT)
    assert "Suspected rail crack in Delhi division" in core
    assert "inspect immediately" in core
    assert "SEG-DLI-012" not in core  # IDs are appended, never translated
    body = sms_body(core, INCIDENT, "https://ack.example/?c=abc")
    assert body.startswith("RailPulse P1 | ")
    assert body.endswith("SEG-DLI-012 km 11.0 ACK: https://ack.example/?c=abc")


def test_translator_skips_english_and_caches():
    fake = FakeTranslate()
    t = Translator(fake)
    assert t("hello", "en") == "hello"
    assert t("hello", "ta") == t("hello", "ta") == "[ta] hello"
    assert fake.calls == 1
