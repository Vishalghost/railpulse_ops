import time
from decimal import Decimal
from types import SimpleNamespace

import boto3
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from moto import mock_aws

from app.auth import TokenVerifier
from app.live import LiveHub
from app.main import app, get_services
from app.repo import Repo

REGION = "ap-south-1"
POOL = "ap-south-1_TEST"
CLIENT = "test-client"
NOW_MS = int(time.time() * 1000)
T0 = NOW_MS - 600_000  # incidents started 10 min ago

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeJwks:
    def get_signing_key_from_jwt(self, token):
        return SimpleNamespace(key=_key.public_key())


def token(groups=(), *, username="tester", token_use="access", client_id=CLIENT, exp_in=3600, key=None) -> str:
    claims = {
        "sub": f"sub-{username}", "username": username, "token_use": token_use, "client_id": client_id,
        "iss": f"https://cognito-idp.{REGION}.amazonaws.com/{POOL}",
        "exp": int(time.time()) + exp_in, "cognito:groups": list(groups),
    }
    return jwt.encode(claims, key or _key, algorithm="RS256", headers={"kid": "test"})


def auth(groups=(), **kw) -> dict:
    return {"Authorization": f"Bearer {token(groups, **kw)}"}


DLI_SSE = ("SSE", "DIV_DLI")
DLI_GANGMAN = ("Gangman", "DIV_DLI")
ADMIN = ("Admin",)


def _table(ddb, name, gsi=False):
    attrs = [{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}]
    kw = {}
    if gsi:
        attrs += [{"AttributeName": "gsi1pk", "AttributeType": "S"}, {"AttributeName": "gsi1sk", "AttributeType": "S"}]
        kw["GlobalSecondaryIndexes"] = [{
            "IndexName": "gsi1",
            "KeySchema": [{"AttributeName": "gsi1pk", "KeyType": "HASH"}, {"AttributeName": "gsi1sk", "KeyType": "RANGE"}],
            "Projection": {"ProjectionType": "ALL"},
        }]
    return ddb.create_table(TableName=name, BillingMode="PAY_PER_REQUEST", AttributeDefinitions=attrs,
                            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"},
                                       {"AttributeName": "sk", "KeyType": "RANGE"}], **kw)


def incident(segment, type_, severity, status, first_seen, division):
    iid = f"{segment}:{type_}:{first_seen}"
    sk = f"INC#{first_seen:013d}#{type_}"
    return {
        "pk": f"SEG#{segment}", "sk": sk, "incident_id": iid, "type": type_, "status": status,
        "severity": severity, "segment_id": segment, "division": division, "first_seen": first_seen,
        "last_seen": first_seen, "occurrences": 3, "sensor_ids": {f"{segment[4:]}-UTS"},
        "gsi1pk": f"DIV#{division}", "gsi1sk": f"STATUS#{status}#{first_seen:013d}",
    }


CRACK = incident("SEG-DLI-012", "CRACK_SUSPECTED", 1, "OPEN", T0, "DLI")
BUCKLE = incident("SEG-DLI-004", "BUCKLING_RISK", 3, "ACKNOWLEDGED", T0 + 1000, "DLI")
SWITCH = incident("SEG-MAS-005", "SWITCH_DEGRADED", 2, "OPEN", T0 + 2000, "MAS")


@pytest.fixture
def tables(monkeypatch):
    for k, v in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
                 "AWS_DEFAULT_REGION": REGION}.items():
        monkeypatch.setenv(k, v)
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name=REGION)
        inc, assets, tel = _table(ddb, "Incidents", gsi=True), _table(ddb, "Assets"), _table(ddb, "Telemetry")
        for i in (CRACK, BUCKLE, SWITCH):
            inc.put_item(Item=i)
            inc.put_item(Item={"pk": i["pk"], "sk": f"ACTIVE#{i['type']}", "incident_sk": i["sk"],
                               "incident_id": i["incident_id"]})
        inc.put_item(Item={"pk": CRACK["pk"], "sk": f"NOTIFY#{T0 // 1000 + 5:010d}#0#GANGMAN",
                           "incident_id": CRACK["incident_id"], "level": 0, "role": "GANGMAN",
                           "awaiting_ack": True, "results": [], "at": T0 // 1000 + 5})
        for n in range(1, 23):
            seg = f"SEG-DLI-{n:03d}"
            assets.put_item(Item={"pk": "DIV#DLI", "sk": f"SEG#{seg}", "segment_id": seg,
                                  "km_from": Decimal(n - 1), "km_to": Decimal(n),
                                  "lat": Decimal("28.64"), "lon": Decimal("77.21")})
        assets.put_item(Item={"pk": "SEG#SEG-DLI-012", "sk": "META", "segment_id": "SEG-DLI-012",
                              "division": "DLI", "stress_free_temp_c": Decimal(35)})
        assets.put_item(Item={"pk": "SEG#SEG-DLI-012", "sk": "SENSOR#DLI-012-UTS", "sensor_id": "DLI-012-UTS",
                              "type": "UTS", "division": "DLI"})
        for k in range(5):
            ts = NOW_MS - (5 - k) * 5000
            tel.put_item(Item={"pk": "SENSOR#DLI-012-UTS", "sk": f"TS#{ts:013d}",
                               "metrics": {"backwall_echo_db": Decimal("78.0") - k}})
        tel.put_item(Item={"pk": "SENSOR#DLI-012-UTS", "sk": f"TS#{NOW_MS - 10 * 3_600_000:013d}",
                           "metrics": {"backwall_echo_db": Decimal("78.0")}})  # outside default 6 h window
        yield {"incidents": inc, "assets": assets, "telemetry": tel}


class FakeServices:
    def __init__(self, tables):
        self.repo = Repo(tables["incidents"], tables["assets"], tables["telemetry"])
        self.verifier = TokenVerifier(REGION, POOL, CLIENT, jwks_client=FakeJwks())
        self.hub = LiveHub(self.repo.active_incidents, interval_s=0.05)

    def public_config(self):
        return {"region": REGION, "userPoolId": POOL, "clientId": CLIENT,
                "cognitoDomain": "https://example.auth", "mapStyleUrl": None}


@pytest.fixture
def services(tables):
    return FakeServices(tables)


@pytest.fixture
def client(services):
    app.dependency_overrides[get_services] = lambda: services
    yield TestClient(app)
    app.dependency_overrides.clear()
