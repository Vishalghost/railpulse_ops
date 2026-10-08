from boto3.dynamodb.conditions import Key

from .conftest import ADMIN, BUCKLE, CRACK, DLI_GANGMAN, DLI_SSE, SWITCH, _key, auth, token

# --- auth ---------------------------------------------------------------------------------


def test_health_and_config_are_public(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/config").json()["clientId"] == "test-client"


def test_missing_or_invalid_tokens_are_401(client):
    from cryptography.hazmat.primitives.asymmetric import rsa
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    bad = [
        {},
        {"Authorization": "Bearer not-a-jwt"},
        auth(DLI_SSE, token_use="id"),           # ID token, not access token
        auth(DLI_SSE, client_id="other-app"),   # another app client's token
        auth(DLI_SSE, exp_in=-60),              # expired
        auth(DLI_SSE, key=other_key),           # forged signature
    ]
    for headers in bad:
        assert client.get("/api/me", headers=headers).status_code == 401


def test_me_reports_roles_and_divisions(client):
    assert client.get("/api/me", headers=auth(DLI_SSE)).json() == {
        "username": "tester", "roles": ["SSE"], "divisions": ["DLI"], "canResolve": True}
    admin = client.get("/api/me", headers=auth(ADMIN)).json()
    assert admin["divisions"] == ["DLI", "MAS", "BB"]


# --- incidents ------------------------------------------------------------------------------


def test_list_incidents_is_division_scoped_and_hides_keys(client):
    items = client.get("/api/divisions/DLI/incidents", headers=auth(DLI_SSE)).json()["items"]
    assert [i["incident_id"] for i in items] == [CRACK["incident_id"]]
    assert not {"pk", "sk", "gsi1pk", "gsi1sk"} & items[0].keys()
    assert client.get("/api/divisions/MAS/incidents", headers=auth(DLI_SSE)).status_code == 403
    assert client.get("/api/divisions/MAS/incidents", headers=auth(ADMIN)).status_code == 200


def test_list_by_status(client):
    acked = client.get("/api/divisions/DLI/incidents?status=ACKNOWLEDGED", headers=auth(DLI_SSE)).json()["items"]
    assert [i["incident_id"] for i in acked] == [BUCKLE["incident_id"]]
    assert client.get("/api/divisions/DLI/incidents?status=BOGUS", headers=auth(DLI_SSE)).status_code == 422


def test_map_marks_worst_active_severity_per_segment(client):
    segs = {s["segment_id"]: s for s in client.get("/api/divisions/DLI/map", headers=auth(DLI_SSE)).json()["segments"]}
    assert len(segs) == 22
    assert segs["SEG-DLI-012"]["worst_severity"] == 1
    assert segs["SEG-DLI-004"]["worst_severity"] == 3  # acknowledged still counts as active
    assert segs["SEG-DLI-001"]["worst_severity"] is None


def test_incident_detail_includes_timeline_and_segment(client):
    body = client.get(f"/api/incidents/{CRACK['incident_id']}", headers=auth(DLI_SSE)).json()
    assert body["incident"]["severity"] == 1
    assert [t["role"] for t in body["timeline"]] == ["GANGMAN"]
    assert body["segment"]["stress_free_temp_c"] == 35


def test_cross_division_and_malformed_incident_ids(client):
    assert client.get(f"/api/incidents/{SWITCH['incident_id']}", headers=auth(DLI_SSE)).status_code == 403
    assert client.get("/api/incidents/nonsense", headers=auth(DLI_SSE)).status_code == 404
    assert client.get("/api/incidents/SEG-DLI-099:CRACK_SUSPECTED:1790000000000",
                      headers=auth(DLI_SSE)).status_code == 404


def test_acknowledge_once(client):
    url = f"/api/incidents/{CRACK['incident_id']}/acknowledge"
    r = client.post(url, headers=auth(DLI_GANGMAN, username="ramesh"))
    assert r.status_code == 200
    assert (r.json()["incident"]["status"], r.json()["incident"]["acknowledged_by"]) == ("ACKNOWLEDGED", "ramesh")
    assert client.post(url, headers=auth(DLI_GANGMAN)).status_code == 409


def test_resolve_requires_role_and_frees_the_segment(client, tables):
    url = f"/api/incidents/{CRACK['incident_id']}/resolve"
    assert client.post(url, json={}, headers=auth(DLI_GANGMAN)).status_code == 403

    r = client.post(url, json={"note": "rail replaced"}, headers=auth(DLI_SSE, username="sse-dli"))
    assert r.status_code == 200
    inc = r.json()["incident"]
    assert (inc["status"], inc["resolved_by"], inc["resolution_note"]) == ("RESOLVED", "sse-dli", "rail replaced")

    # Pointer gone → the worker opens a fresh incident on the next detection.
    assert "Item" not in tables["incidents"].get_item(Key={"pk": CRACK["pk"], "sk": "ACTIVE#CRACK_SUSPECTED"})
    resolved = tables["incidents"].query(
        IndexName="gsi1", KeyConditionExpression=Key("gsi1pk").eq("DIV#DLI") & Key("gsi1sk").begins_with("STATUS#RESOLVED#"))
    assert len(resolved["Items"]) == 1
    assert client.post(url, json={}, headers=auth(DLI_SSE)).status_code == 409


def test_resolve_keeps_a_pointer_that_belongs_to_a_newer_incident(client, tables):
    tables["incidents"].put_item(Item={"pk": CRACK["pk"], "sk": "ACTIVE#CRACK_SUSPECTED",
                                       "incident_sk": "INC#9999999999999#CRACK_SUSPECTED"})
    r = client.post(f"/api/incidents/{CRACK['incident_id']}/resolve", json={}, headers=auth(DLI_SSE))
    assert r.status_code == 409  # transaction refused: don't orphan the newer incident


def test_sensor_health_snapshot(client, tables):
    url = "/api/divisions/DLI/sensor-health"
    assert client.get(url, headers=auth(DLI_SSE)).json() == {"health": None}
    tables["assets"].put_item(Item={"pk": "DIV#DLI", "sk": "HEALTH", "checked_at": 1790000000,
                                    "total": 70, "reporting": 67, "silent": ["DLI-002-UTS"], "not_reporting": []})
    health = client.get(url, headers=auth(DLI_SSE)).json()["health"]
    assert (health["total"], health["reporting"], health["silent"]) == (70, 67, ["DLI-002-UTS"])
    assert client.get("/api/divisions/MAS/sensor-health", headers=auth(DLI_SSE)).status_code == 403


# --- telemetry ------------------------------------------------------------------------------


def test_telemetry_window_and_order(client):
    body = client.get("/api/segments/SEG-DLI-012/telemetry", headers=auth(DLI_SSE)).json()
    [sensor] = body["sensors"]
    points = sensor["points"]
    assert len(points) == 5  # the 10 h-old point is outside the 6 h window
    assert [p["ts"] for p in points] == sorted(p["ts"] for p in points)
    assert points[-1]["metrics"]["backwall_echo_db"] == 74
    assert client.get("/api/segments/SEG-MAS-001/telemetry", headers=auth(DLI_SSE)).status_code == 403
