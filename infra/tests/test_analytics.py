"""Offline checks for Phase 6 analytics: every saved Athena query parses (sqlglot, Athena
dialect) and only references columns/struct fields the Glue tables actually declare; the Glue
schema keeps up with the simulator's metrics; the stack wires projection, KMS and a scan cap."""

import json
import sys
from pathlib import Path

import aws_cdk as cdk
import pytest
import sqlglot
from aws_cdk.assertions import Match, Template
from sqlglot import exp

from railpulse_infra import analytics_schema as schema
from railpulse_infra.analytics_stack import SCAN_CUTOFF_BYTES, load_queries
from railpulse_infra.build import build_app

QUERIES = load_queries()
STRUCTS = {"metrics": schema.METRIC_FIELDS, "detail": schema.INCIDENT_FIELDS}


def test_there_are_queries():
    assert len(QUERIES) >= 5


@pytest.mark.parametrize("q", QUERIES, ids=lambda q: q["file"])
def test_query_parses_and_uses_declared_columns(q):
    tree = sqlglot.parse_one(q["sql"], read="athena")

    ctes = {c.alias for c in tree.find_all(exp.CTE)}
    tables = {t.name for t in tree.find_all(exp.Table)} - ctes
    assert tables <= set(schema.TABLES), f"unknown table(s) {tables - set(schema.TABLES)}"

    known = set().union(*(schema.TABLES[t] for t in tables))
    aliases = {a.alias for a in tree.find_all(exp.Alias)}
    table_aliases = {t.alias for t in tree.find_all(exp.Table) if t.alias}

    for col in tree.find_all(exp.Column):
        if col.table in STRUCTS:  # struct field access: metrics.x / detail.x
            assert col.table in known, f"{col.table} not a column of {tables}"
            assert col.name in STRUCTS[col.table], f"{col.table}.{col.name} not declared in the Glue struct"
        elif col.table in table_aliases or not col.table:
            assert col.name in known | aliases, f"unknown column {col.sql()}"
        else:
            pytest.fail(f"unexpected qualifier in {col.sql()}")


@pytest.mark.parametrize("q", QUERIES, ids=lambda q: q["file"])
def test_query_prunes_partitions(q):
    # Every query must bound `dt`, or it scans the whole lake (and trips the 1 GB cap).
    tree = sqlglot.parse_one(q["sql"], read="athena")
    assert any(c.name == "dt" for c in tree.find_all(exp.Column)), "query never filters on dt"


def test_glue_struct_covers_every_simulator_metric():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "simulator"))
    from datetime import datetime, timezone

    from railpulse_sim.fleet import build_fleet
    from railpulse_sim.models import ReadingGenerator

    fleet = build_fleet()
    gen = ReadingGenerator(fleet.sensors)
    seen: set[str] = set()
    for hour in range(24):  # covers train/no-train branches
        when = datetime(2026, 1, 15, hour, tzinfo=timezone.utc)
        for s in fleet.sensors:
            seen |= gen.metrics(s, when).keys()
    assert seen == set(schema.METRIC_FIELDS), f"schema drift: {seen ^ set(schema.METRIC_FIELDS)}"


@pytest.fixture(scope="module")
def t():
    cfg = json.load(open("cdk.json"))
    rp = build_app(cdk.App(context=cfg["context"]), account="123456789012", region="ap-south-1")
    return Template.from_stack(rp.analytics)


def test_tables_use_partition_projection_not_crawlers(t):
    t.resource_count_is("AWS::Glue::Crawler", 0)
    t.has_resource_properties("AWS::Glue::Table", {"TableInput": Match.object_like({
        "Name": "telemetry_raw",
        "Parameters": Match.object_like({"projection.enabled": "true", "projection.division.values": "DLI,BB,BCT,MAS,HWH,SC,SBC,LKO",
                                         "projection.dt.type": "date"}),
    })})
    t.has_resource_properties("AWS::Glue::Table", {"TableInput": Match.object_like({"Name": "incident_events"})})


def test_workgroup_caps_scans_and_encrypts_results(t):
    t.has_resource_properties("AWS::Athena::WorkGroup", {"WorkGroupConfiguration": Match.object_like({
        "EnforceWorkGroupConfiguration": True,
        "BytesScannedCutoffPerQuery": SCAN_CUTOFF_BYTES,
        "ResultConfiguration": Match.object_like({
            "EncryptionConfiguration": Match.object_like({"EncryptionOption": "SSE_KMS"})}),
    })})
    t.resource_count_is("AWS::Athena::NamedQuery", len(QUERIES))


def test_detection_events_archived_to_lake(t):
    t.has_resource_properties("AWS::Events::Rule", {"EventPattern": {"source": ["railpulse.detection"]}})
    t.has_resource_properties("AWS::KinesisFirehose::DeliveryStream", {
        "ExtendedS3DestinationConfiguration": Match.object_like({"Prefix": "events/dt=!{timestamp:yyyy-MM-dd}/"})})
