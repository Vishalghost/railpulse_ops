from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_iam as iam
from aws_cdk import aws_iot as iot
from aws_cdk import aws_kinesisfirehose as firehose
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_sqs as sqs
from constructs import Construct

# railpulse/<division>/<sensor_id>/telemetry
TELEMETRY_TOPIC_FILTER = "railpulse/+/+/telemetry"

# division and sensor_id come from the topic, not the payload: the IoT policy pins each
# device to its own topic, so these fields can't be spoofed by a compromised sensor.
TELEMETRY_RULE_SQL = (
    "SELECT *, topic(2) AS division, topic(3) AS sensor_id, timestamp() AS ingest_ts "
    f"FROM '{TELEMETRY_TOPIC_FILTER}'"
)

SIMULATOR_CLIENT_PREFIX = "railpulse-sim-"


class IngestLayer(Construct):
    """IoT Core → (SQS for detection, Firehose → S3 for the lake)."""

    def __init__(
        self, scope: Construct, construct_id: str, *, lake_bucket: s3.IBucket, ephemeral: bool
    ) -> None:
        super().__init__(scope, construct_id)
        stack = Stack.of(self)

        # --- Detection queue -------------------------------------------------------------
        self.dlq = sqs.Queue(
            self,
            "TelemetryDlq",
            retention_period=Duration.days(14),
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            enforce_ssl=True,
        )
        self.queue = sqs.Queue(
            self,
            "TelemetryQueue",
            # Must exceed the worker's worst-case /process time, or sqsd will see redeliveries.
            visibility_timeout=Duration.seconds(120),
            retention_period=Duration.days(4),
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            enforce_ssl=True,
            dead_letter_queue=sqs.DeadLetterQueue(queue=self.dlq, max_receive_count=5),
        )

        # --- Firehose → S3 lake ------------------------------------------------------------
        firehose_role = iam.Role(
            self, "FirehoseRole", assumed_by=iam.ServicePrincipal("firehose.amazonaws.com")
        )
        lake_bucket.grant_read_write(firehose_role)

        log_group = logs.LogGroup(
            self,
            "FirehoseLogs",
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.DESTROY if ephemeral else RemovalPolicy.RETAIN,
        )
        log_stream = logs.LogStream(self, "FirehoseS3Delivery", log_group=log_group)
        log_group.grant_write(firehose_role)

        F = firehose.CfnDeliveryStream
        self.delivery_stream = F(
            self,
            "RawToLake",
            delivery_stream_type="DirectPut",
            extended_s3_destination_configuration=F.ExtendedS3DestinationConfigurationProperty(
                bucket_arn=lake_bucket.bucket_arn,
                role_arn=firehose_role.role_arn,
                # Partitioned for Athena: WHERE division = 'DLI' AND dt = '2026-10-07'
                prefix="raw/division=!{partitionKeyFromQuery:division}/dt=!{timestamp:yyyy-MM-dd}/",
                error_output_prefix="errors/firehose/!{firehose:error-output-type}/dt=!{timestamp:yyyy-MM-dd}/",
                # 64 MB is the minimum buffer size when dynamic partitioning is on.
                buffering_hints=F.BufferingHintsProperty(interval_in_seconds=60, size_in_m_bs=64),
                compression_format="GZIP",
                dynamic_partitioning_configuration=F.DynamicPartitioningConfigurationProperty(
                    enabled=True
                ),
                processing_configuration=F.ProcessingConfigurationProperty(
                    enabled=True,
                    processors=[
                        F.ProcessorProperty(
                            type="MetadataExtraction",
                            parameters=[
                                F.ProcessorParameterProperty(
                                    parameter_name="MetadataExtractionQuery",
                                    parameter_value="{division: .division}",
                                ),
                                F.ProcessorParameterProperty(
                                    parameter_name="JsonParsingEngine", parameter_value="JQ-1.6"
                                ),
                            ],
                        ),
                        # Newline-delimited JSON so Athena reads one record per line.
                        F.ProcessorProperty(type="AppendDelimiterToRecord"),
                    ],
                ),
                cloud_watch_logging_options=F.CloudWatchLoggingOptionsProperty(
                    enabled=True,
                    log_group_name=log_group.log_group_name,
                    log_stream_name=log_stream.log_stream_name,
                ),
            ),
        )
        self.delivery_stream.node.add_dependency(firehose_role)

        # --- IoT Rule: fan out every telemetry message to SQS and Firehose ------------------
        rule_role = iam.Role(self, "IotRuleRole", assumed_by=iam.ServicePrincipal("iot.amazonaws.com"))
        self.queue.grant_send_messages(rule_role)
        rule_role.add_to_policy(
            iam.PolicyStatement(
                actions=["firehose:PutRecord", "firehose:PutRecordBatch"],
                resources=[self.delivery_stream.attr_arn],
            )
        )
        lake_bucket.grant_put(rule_role, "errors/iot-rule/*")

        R = iot.CfnTopicRule
        self.rule = R(
            self,
            "TelemetryRule",
            topic_rule_payload=R.TopicRulePayloadProperty(
                sql=TELEMETRY_RULE_SQL,
                aws_iot_sql_version="2016-03-23",
                rule_disabled=False,
                actions=[
                    R.ActionProperty(
                        sqs=R.SqsActionProperty(
                            queue_url=self.queue.queue_url, role_arn=rule_role.role_arn, use_base64=False
                        )
                    ),
                    R.ActionProperty(
                        firehose=R.FirehoseActionProperty(
                            delivery_stream_name=self.delivery_stream.ref, role_arn=rule_role.role_arn
                        )
                    ),
                ],
                # Failed deliveries land in S3 instead of vanishing silently.
                error_action=R.ActionProperty(
                    s3=R.S3ActionProperty(
                        bucket_name=lake_bucket.bucket_name,
                        key="errors/iot-rule/${newuuid()}.json",
                        role_arn=rule_role.role_arn,
                    )
                ),
            ),
        )
        self.rule.node.add_dependency(rule_role)

        # --- IoT policies ------------------------------------------------------------------
        iot_arn = f"arn:{stack.partition}:iot:{stack.region}:{stack.account}"

        # Production sensors: one cert per Thing; a sensor may only publish as itself.
        self.sensor_policy = iot.CfnPolicy(
            self,
            "SensorPolicy",
            policy_document={
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": "iot:Connect",
                        "Resource": f"{iot_arn}:client/${{iot:Connection.Thing.ThingName}}",
                    },
                    {
                        "Effect": "Allow",
                        "Action": "iot:Publish",
                        "Resource": f"{iot_arn}:topic/railpulse/*/${{iot:Connection.Thing.ThingName}}/telemetry",
                    },
                ],
            },
        )

        # Simulator: a single "gateway" cert that may fan out over several connections,
        # because IoT Core caps each connection at 100 publishes/second.
        self.simulator_policy = iot.CfnPolicy(
            self,
            "SimulatorPolicy",
            policy_document={
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": "iot:Connect",
                        "Resource": f"{iot_arn}:client/{SIMULATOR_CLIENT_PREFIX}*",
                    },
                    {
                        "Effect": "Allow",
                        "Action": "iot:Publish",
                        "Resource": f"{iot_arn}:topic/railpulse/*/*/telemetry",
                    },
                ],
            },
        )
