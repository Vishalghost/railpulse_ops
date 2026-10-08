import json

import pytest
from boto3.dynamodb.conditions import Key

from railpulse_alerting.channels import SmsSender, TopicPublisher
from railpulse_alerting.handlers import ack, notify, start
from railpulse_alerting.messages import Translator

from .conftest import INCIDENT, KEY, FakeSfn, FakeSms, FakeSns, FakeTranslate, FakeVoice

SM_ARN = "arn:aws:states:ap-south-1:123456789012:stateMachine:railpulse-dev-escalation"
STEP = {"role": "GANGMAN", "ack_timeout_s": 600, "contacts": [
    {"contact_id": "gm-1", "name": "Ramesh", "phone": "+910000000001", "lang": "hi"},
    {"contact_id": "gm-2", "name": "Suresh", "phone": "+910000000002", "lang": "hi"},
]}


# --- start ------------------------------------------------------------------------------------

def seed_contacts(assets):
    assets.put_item(Item={"pk": "DIV#DLI", "sk": "CONTACT#GANGMAN#gm-1", "contact_id": "gm-1",
                          "role": "GANGMAN", "lang": "hi", "phone": "+910000000001"})
    assets.put_item(Item={"pk": "DIV#DLI", "sk": "CONTACT#SSE#sse-1", "contact_id": "sse-1",
                          "role": "SSE", "lang": "en", "phone": "+910000000003"})


def test_start_is_idempotent_per_incident_and_severity(tables):
    seed_contacts(tables["Assets"])
    sfn = FakeSfn()
    event = {"detail": INCIDENT}
    first = start.start(event, tables["Assets"], sfn, SM_ARN, None)
    again = start.start(event, tables["Assets"], sfn, SM_ARN, None)
    escalated = start.start({"detail": {**INCIDENT, "severity": 2}}, tables["Assets"], sfn, SM_ARN, None)

    assert first["started"] and not again["started"] and again["reason"] == "duplicate"
    assert escalated["started"] and len(sfn.started) == 2
    payload = json.loads(sfn.started[first["execution"]])
    assert payload["key"] == KEY
    assert [r["role"] for r in payload["ladder"]] == ["GANGMAN", "SSE"]


def test_execution_name_is_valid():
    name = start.execution_name(INCIDENT)
    assert name == "SEG-DLI-012-CRACK_SUSPECTED-1790000000000-P1"
    assert len(name) <= 80


def test_start_without_contacts_does_nothing(tables):
    sfn = FakeSfn()
    assert start.start({"detail": INCIDENT}, tables["Assets"], sfn, SM_ARN, None)["reason"] == "no_ladder"
    assert sfn.started == {}


# --- notify -----------------------------------------------------------------------------------

def make_notify_deps(tables, sms=None, sns=None):
    return notify.Deps(
        incidents=tables["Incidents"],
        translate=Translator(FakeTranslate()),
        sms=SmsSender(sms or FakeSms(), mode="live"),
        topic=TopicPublisher(sns or FakeSns(), "arn:aws:sns:ap-south-1:123456789012:alerts"),
        voice=FakeVoice(),
        ack_base_url="https://ack.example.lambda-url.ap-south-1.on.aws/",
    )


def ack_items(incidents):
    return [i for i in incidents.scan()["Items"] if i["pk"].startswith("ACK#")]


def test_notify_pages_each_contact_with_own_translated_ack_link(tables):
    sms = FakeSms()
    d = make_notify_deps(tables, sms=sms)
    notify.notify({"incident": INCIDENT, "key": KEY, "step": STEP, "level": 0, "taskToken": "tok-1"}, d)

    assert [m["DestinationPhoneNumber"] for m in sms.sent] == ["+910000000001", "+910000000002"]
    assert all("[hi] Railway alert." in m["MessageBody"] and "ACK: https://ack.example" in m["MessageBody"]
               for m in sms.sent)
    acks = ack_items(tables["Incidents"])
    assert {a["contact_id"] for a in acks} == {"gm-1", "gm-2"}
    assert {a["task_token"] for a in acks} == {"tok-1"}
    assert len({a["pk"] for a in acks}) == 2  # distinct single-use codes

    inc = tables["Incidents"].get_item(Key=KEY)["Item"]
    assert inc["escalation_role"] == "GANGMAN" and inc["escalation_level"] == 0
    log = tables["Incidents"].query(KeyConditionExpression=Key("pk").eq(KEY["pk"]) & Key("sk").begins_with("NOTIFY#"))
    assert log["Items"][0]["awaiting_ack"] is True


def test_inform_only_rung_creates_no_ack_links(tables):
    sms = FakeSms()
    step = {**STEP, "ack_timeout_s": 0}
    notify.notify({"incident": {**INCIDENT, "severity": 3}, "key": KEY, "step": step, "level": 0},
                  make_notify_deps(tables, sms=sms))
    assert ack_items(tables["Incidents"]) == []
    assert "ACK:" not in sms.sent[0]["MessageBody"]


def test_one_bad_number_does_not_block_the_rung(tables):
    sms = FakeSms(fail_numbers={"+910000000001"})
    out = notify.notify({"incident": INCIDENT, "key": KEY, "step": STEP, "level": 0, "taskToken": "t"},
                        make_notify_deps(tables, sms=sms))
    assert [r["status"] for r in out["results"]] == ["failed", "sent"]


def test_unreachable_rung_fails_so_state_machine_escalates(tables):
    d = make_notify_deps(tables, sms=FakeSms(fail_numbers={"+910000000001", "+910000000002"}), sns=FakeSns(fail=True))
    with pytest.raises(RuntimeError):
        notify.notify({"incident": INCIDENT, "key": KEY, "step": STEP, "level": 0, "taskToken": "t"}, d)


# --- ack --------------------------------------------------------------------------------------

def paged(tables):
    notify.notify({"incident": INCIDENT, "key": KEY, "step": STEP, "level": 0, "taskToken": "tok-1"},
                  make_notify_deps(tables))
    return [a["pk"].removeprefix("ACK#") for a in ack_items(tables["Incidents"])]


def req(method, code):
    return {"requestContext": {"http": {"method": method}}, "queryStringParameters": {"c": code}}


def test_get_shows_alert_but_never_acknowledges(tables):
    code = paged(tables)[0]
    sfn = FakeSfn()
    for _ in range(3):  # link previewers / scanners
        page = ack.respond(req("GET", code), tables["Incidents"], sfn, FakeVoice())
        assert page["statusCode"] == 200 and "<form method=\"post\"" in page["body"]
    assert sfn.successes == []
    assert tables["Incidents"].get_item(Key=KEY)["Item"]["status"] == "OPEN"


def test_post_acknowledges_once_and_records_who(tables):
    code_a, code_b = paged(tables)
    sfn = FakeSfn()
    assert ack.respond(req("POST", code_a), tables["Incidents"], sfn, None)["statusCode"] == 200
    again = ack.respond(req("POST", code_a), tables["Incidents"], sfn, None)
    assert "already" in again["body"].lower()
    assert len(sfn.successes) == 1 and json.loads(sfn.successes[0][1])["role"] == "GANGMAN"

    inc = tables["Incidents"].get_item(Key=KEY)["Item"]
    assert inc["status"] == "ACKNOWLEDGED"
    assert inc["gsi1sk"] == "STATUS#ACKNOWLEDGED#1790000000000"
    first_acker = inc["acknowledged_by"]

    # Colleague confirms after the token is spent: recorded as late, original acker kept.
    sfn.success_error = "InvalidToken"
    late = ack.respond(req("POST", code_b), tables["Incidents"], sfn, None)
    assert "escalated" in late["body"]
    assert tables["Incidents"].get_item(Key=KEY)["Item"]["acknowledged_by"] == first_acker


def test_unexpected_sfn_error_releases_the_code_for_retry(tables):
    code = paged(tables)[0]
    sfn = FakeSfn()
    sfn.success_error = "ThrottlingException"
    with pytest.raises(Exception):
        ack.respond(req("POST", code), tables["Incidents"], sfn, None)
    sfn.success_error = None
    assert ack.respond(req("POST", code), tables["Incidents"], sfn, None)["statusCode"] == 200
    assert len(sfn.successes) == 1


def test_ack_refuses_requests_that_bypass_cloudfront(tables):
    code = paged(tables)[0]
    direct = req("GET", code)                                     # straight to the Function URL
    wrong = {**req("GET", code), "headers": {"x-origin-verify": "guess"}}
    via_cdn = {**req("GET", code), "headers": {"x-origin-verify": "s3cret"}}
    for event, status in ((direct, 403), (wrong, 403), (via_cdn, 200)):
        assert ack.respond(event, tables["Incidents"], FakeSfn(), None, expected_secret="s3cret")["statusCode"] == status


def test_ack_check_fails_closed_when_required(monkeypatch):
    monkeypatch.setenv("REQUIRE_ORIGIN_VERIFY", "false")
    assert ack.expected_origin_secret() is None
    monkeypatch.setenv("REQUIRE_ORIGIN_VERIFY", "true")
    monkeypatch.setenv("ORIGIN_VERIFY_SECRET_ID", "railpulse/dev/origin-verify")
    monkeypatch.setattr(ack, "_secret_cache", None)
    from moto import mock_aws
    with mock_aws():  # secret doesn't exist → exception, never "allow everyone"
        with pytest.raises(Exception):
            ack.expected_origin_secret()


def test_ack_links_point_at_cloudfront_url_from_ssm():
    class FakeSsm:
        def get_parameter(self, Name):
            assert Name == "/railpulse/dev/web/ack-base-url"
            return {"Parameter": {"Value": "https://d111.cloudfront.net/ack/"}}

    env = {"ACK_BASE_URL_PARAM": "/railpulse/dev/web/ack-base-url"}
    assert notify.resolve_ack_base_url(FakeSsm(), env) == "https://d111.cloudfront.net/ack/"
    assert notify.resolve_ack_base_url(FakeSsm(), {**env, "ACK_BASE_URL": "http://override/"}) == "http://override/"


@pytest.mark.parametrize("code", ["", "short", "../../etc/pass", "x" * 12])
def test_bad_or_unknown_codes_404(tables, code):
    assert ack.respond(req("GET", code), tables["Incidents"], FakeSfn(), None)["statusCode"] == 404
