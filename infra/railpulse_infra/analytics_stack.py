"""Phase 6: query the data lake with Athena.

    raw/division=…/dt=…/   telemetry (IoT rule → Firehose, Phase 1)      → telemetry_raw
    events/dt=…/           every detection event (bus → Firehose, here)  → incident_events

Partition projection describes the S3 layout inside the table definition, so Athena computes
partition paths itself — no Glue crawler to schedule or pay for, and a new day's data is
queryable the moment Firehose writes it.
"""

import re
from pathlib import Path

from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_athena as athena
from aws_cdk import aws_events as events
from aws_cdk import aws_glue as glue
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kinesisfirehose as firehose
from constructs import Construct

from . import analytics_schema as schema
from .core_stack import RailPulseCoreStack

QUERIES_DIR = Path(__file__).resolve().parents[2] / "analytics" / "queries"
SCAN_CUTOFF_BYTES = 1_000_000_000  # per query: a runaway query costs fractions of a cent


def load_queries() -> list[dict[str, str]]:
    """Each .sql file starts with `-- name:` and `-- description:` header lines."""
    out = []
    for path in sorted(QUERIES_DIR.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        meta = dict(re.findall(r"^-- (name|description): (.+)$", sql, flags=re.MULTILINE))
        out.append({"file": path.name, "name": meta["name"], "description": meta["description"], "sql": sql})
    return out


class RailPulseAnalyticsStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, stage: str, core: RailPulseCoreStack,
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        bucket = core.data.lake_bucket
        db_name = f"railpulse_{stage}"  # Glue/Athena names: no hyphens

        db = glue.CfnDatabase(
            self, "Database", catalog_id=self.account,
            database_input=glue.CfnDatabase.DatabaseInputProperty(
                name=db_name, description="RailPulse telemetry and incident history (S3 data lake)"),
        )

        def table(construct_id: str, name: str, prefix: str, columns, partitions, mappings: dict,
                  projection: dict[str, str], template: str) -> glue.CfnTable:
            col = glue.CfnTable.ColumnProperty
            t = glue.CfnTable(
                self, construct_id, catalog_id=self.account, database_name=db_name,
                table_input=glue.CfnTable.TableInputProperty(
                    name=name,
                    table_type="EXTERNAL_TABLE",
                    parameters={
                        "classification": "json",
                        "compressionType": "gzip",
                        "projection.enabled": "true",
                        **projection,
                        "storage.location.template": f"s3://{bucket.bucket_name}/{prefix}/{template}",
                    },
                    partition_keys=[col(name=n, type=t) for n, t in partitions],
                    storage_descriptor=glue.CfnTable.StorageDescriptorProperty(
                        location=f"s3://{bucket.bucket_name}/{prefix}/",
                        columns=[col(name=n, type=t) for n, t in columns],
                        input_format="org.apache.hadoop.mapred.TextInputFormat",
                        output_format="org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat",
                        serde_info=glue.CfnTable.SerdeInfoProperty(
                            serialization_library="org.openx.data.jsonserde.JsonSerDe",
                            # Skip a corrupt line instead of failing the whole query.
                            parameters={"ignore.malformed.json": "true", **mappings},
                        ),
                    ),
                ),
            )
            t.node.add_dependency(db)
            return t

        dt_projection = {
            "projection.dt.type": "date",
            "projection.dt.format": "yyyy-MM-dd",
            "projection.dt.range": f"{schema.PROJECTION_START},NOW",
            "projection.dt.interval": "1",
            "projection.dt.interval.unit": "DAYS",
        }
        table("TelemetryTable", schema.TELEMETRY_TABLE, "raw",
              schema.TELEMETRY_COLUMNS, schema.TELEMETRY_PARTITIONS, schema.TELEMETRY_MAPPINGS,
              {"projection.division.type": "enum", "projection.division.values": ",".join(schema.DIVISIONS),
               **dt_projection},
              "division=${division}/dt=${dt}/")
        table("EventsTable", schema.EVENTS_TABLE, "events",
              schema.EVENTS_COLUMNS, schema.EVENTS_PARTITIONS, schema.EVENTS_MAPPINGS,
              dt_projection, "dt=${dt}/")

        # --- Detection events → S3 (incident history the DynamoDB table doesn't keep forever) ---
        fh_role = iam.Role(self, "EventsFirehoseRole", assumed_by=iam.ServicePrincipal("firehose.amazonaws.com"))
        bucket.grant_read_write(fh_role)  # includes the KMS key
        F = firehose.CfnDeliveryStream
        stream = F(
            self, "EventsToLake",
            delivery_stream_type="DirectPut",
            extended_s3_destination_configuration=F.ExtendedS3DestinationConfigurationProperty(
                bucket_arn=bucket.bucket_arn,
                role_arn=fh_role.role_arn,
                prefix="events/dt=!{timestamp:yyyy-MM-dd}/",
                error_output_prefix="errors/events/!{firehose:error-output-type}/dt=!{timestamp:yyyy-MM-dd}/",
                buffering_hints=F.BufferingHintsProperty(interval_in_seconds=300, size_in_m_bs=5),
                compression_format="GZIP",
                processing_configuration=F.ProcessingConfigurationProperty(
                    enabled=True, processors=[F.ProcessorProperty(type="AppendDelimiterToRecord")]),
            ),
        )
        stream.node.add_dependency(fh_role)

        rule_role = iam.Role(self, "EventsRuleRole", assumed_by=iam.ServicePrincipal("events.amazonaws.com"))
        rule_role.add_to_policy(iam.PolicyStatement(actions=["firehose:PutRecord", "firehose:PutRecordBatch"],
                                                    resources=[stream.attr_arn]))
        events.CfnRule(
            self, "DetectionEventsToLake",
            description="Archive every detection event to the data lake",
            event_bus_name=core.bus.event_bus_name,
            event_pattern={"source": ["railpulse.detection"]},
            targets=[events.CfnRule.TargetProperty(id="lake", arn=stream.attr_arn, role_arn=rule_role.role_arn)],
        )

        # --- Athena workgroup + saved queries --------------------------------------------------
        wg = athena.CfnWorkGroup(
            self, "WorkGroup",
            name=f"railpulse-{stage}",
            description="RailPulse analytics (results encrypted, 1 GB scan cap per query)",
            recursive_delete_option=True,
            work_group_configuration=athena.CfnWorkGroup.WorkGroupConfigurationProperty(
                enforce_work_group_configuration=True,
                publish_cloud_watch_metrics_enabled=True,
                bytes_scanned_cutoff_per_query=SCAN_CUTOFF_BYTES,
                engine_version=athena.CfnWorkGroup.EngineVersionProperty(selected_engine_version="AUTO"),
                result_configuration=athena.CfnWorkGroup.ResultConfigurationProperty(
                    output_location=f"s3://{bucket.bucket_name}/athena-results/",
                    encryption_configuration=athena.CfnWorkGroup.EncryptionConfigurationProperty(
                        encryption_option="SSE_KMS", kms_key=core.key.key_arn),
                ),
            ),
        )
        for i, q in enumerate(load_queries()):
            nq = athena.CfnNamedQuery(
                self, f"Query{i}", database=db_name, work_group=wg.name,
                name=q["name"], description=q["description"][:1024], query_string=q["sql"],
            )
            nq.node.add_dependency(wg, db)

        CfnOutput(self, "AthenaWorkGroup", value=wg.name)
        CfnOutput(self, "GlueDatabase", value=db_name)
