import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from app.main import app, get_processor
from app.store import AssetRepo, BaselineRepo, IncidentRepo, TelemetryRepo
from app.processor import Processor

REGION = "ap-south-1"


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self.fail_next = 0

    def publish(self, detail_type: str, incident: dict) -> None:
        if self.fail_next:
            self.fail_next -= 1
            raise RuntimeError("simulated EventBridge outage")
        self.events.append((detail_type, incident))


def _create_table(ddb, name: str, gsi: bool = False):
    kwargs = dict(
        TableName=name,
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                              {"AttributeName": "sk", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    if gsi:
        kwargs["AttributeDefinitions"] += [{"AttributeName": "gsi1pk", "AttributeType": "S"},
                                           {"AttributeName": "gsi1sk", "AttributeType": "S"}]
        kwargs["GlobalSecondaryIndexes"] = [{
            "IndexName": "gsi1",
            "KeySchema": [{"AttributeName": "gsi1pk", "KeyType": "HASH"},
                          {"AttributeName": "gsi1sk", "KeyType": "RANGE"}],
            "Projection": {"ProjectionType": "ALL"},
        }]
    return ddb.create_table(**kwargs)


@pytest.fixture
def aws(monkeypatch):
    for k, v in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                 "AWS_DEFAULT_REGION": REGION}.items():
        monkeypatch.setenv(k, v)
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name=REGION)
        tables = {
            "telemetry": _create_table(ddb, "Telemetry"),
            "incidents": _create_table(ddb, "Incidents", gsi=True),
            "assets": _create_table(ddb, "Assets"),
            "baselines": _create_table(ddb, "Baselines"),
        }
        tables["assets"].put_item(Item={"pk": "SEG#SEG-DLI-001", "sk": "META", "division": "DLI",
                                        "stress_free_temp_c": 35, "km_from": 0})
        yield tables


@pytest.fixture
def publisher():
    return RecordingPublisher()


@pytest.fixture
def client(aws, publisher):
    processor = Processor(
        telemetry=TelemetryRepo(aws["telemetry"], ttl_days=30),
        baselines=BaselineRepo(aws["baselines"]),
        assets=AssetRepo(aws["assets"]),
        incidents=IncidentRepo(aws["incidents"]),
        events=publisher,
    )
    app.dependency_overrides[get_processor] = lambda: processor
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()
