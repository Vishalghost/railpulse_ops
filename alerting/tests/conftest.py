from decimal import Decimal

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

REGION = "ap-south-1"

INCIDENT = {
    "incident_id": "SEG-DLI-012:CRACK_SUSPECTED:1790000000000",
    "type": "CRACK_SUSPECTED",
    "status": "OPEN",
    "severity": 1,
    "segment_id": "SEG-DLI-012",
    "division": "DLI",
    "km_from": 11.0,
    "first_seen": 1790000000000,
    "last_seen": 1790000030000,
    "occurrences": 6,
}
KEY = {"pk": "SEG#SEG-DLI-012", "sk": "INC#1790000000000#CRACK_SUSPECTED"}


def client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "op")


class FakeTranslate:
    def __init__(self):
        self.calls = 0

    def translate_text(self, Text, SourceLanguageCode, TargetLanguageCode):
        self.calls += 1
        return {"TranslatedText": f"[{TargetLanguageCode}] {Text}"}


class FakeSms:
    def __init__(self, fail_numbers=()):
        self.sent = []
        self.fail_numbers = set(fail_numbers)

    def send_text_message(self, **kw):
        if kw["DestinationPhoneNumber"] in self.fail_numbers:
            raise client_error("ValidationException")
        self.sent.append(kw)
        return {"MessageId": f"m{len(self.sent)}"}


class FakeSns:
    def __init__(self, fail=False):
        self.published = []
        self.fail = fail

    def publish(self, **kw):
        if self.fail:
            raise client_error("InternalError")
        self.published.append(kw)
        return {"MessageId": "sns-1"}


class FakeVoice:
    def __init__(self):
        self.rendered = []

    def render(self, key, text, lang, fallback_text):
        self.rendered.append((key, lang))
        return key

    def presign(self, key, expires_s=3600):
        return f"https://example.invalid/{key}"


class FakeSfn:
    def __init__(self):
        self.started = {}
        self.successes = []
        self.success_error = None

    def start_execution(self, stateMachineArn, name, input):
        if name in self.started:
            raise client_error("ExecutionAlreadyExists")
        self.started[name] = input
        return {"executionArn": f"{stateMachineArn}:{name}"}

    def send_task_success(self, taskToken, output):
        if self.success_error:
            raise client_error(self.success_error)
        self.successes.append((taskToken, output))


@pytest.fixture
def tables(monkeypatch):
    for k, v in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                 "AWS_DEFAULT_REGION": REGION}.items():
        monkeypatch.setenv(k, v)
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name=REGION)
        out = {}
        for name in ("Incidents", "Assets"):
            out[name] = ddb.create_table(
                TableName=name,
                KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
                AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                                      {"AttributeName": "sk", "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST",
            )
        out["Incidents"].put_item(Item={**KEY, **INCIDENT, "km_from": Decimal("11.0")})
        yield out
