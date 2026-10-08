from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_events as events
from aws_cdk import aws_kms as kms
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from constructs import Construct

from .data import DataLayer
from .ingest import IngestLayer
from .network import NetworkLayer


class RailPulseCoreStack(Stack):
    """Network, encryption key, storage, ingest, event bus and the ops alarm topic.
    Long-lived; the EB environments and Lambdas live in their own stacks."""

    def __init__(self, scope: Construct, construct_id: str, *, stage: str, ops_email: str | None = None,
                 lean: bool = True, nat_gateways: int = 1, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        ephemeral = stage != "prod"
        removal = RemovalPolicy.DESTROY if ephemeral else RemovalPolicy.RETAIN

        self.key = kms.Key(
            self, "DataKey",
            alias=f"alias/railpulse-{stage}-data",
            description="RailPulse telemetry, incidents, assets and data lake",
            enable_key_rotation=True,
            # Deleting a key makes its data unreadable forever; even dev waits the minimum 7 days.
            pending_window=Duration.days(7),
            removal_policy=removal,
        )
        self.network = NetworkLayer(self, "Network", lean=lean, nat_gateways=nat_gateways)
        self.data = DataLayer(self, "Data", ephemeral=ephemeral, key=self.key)
        self.ingest = IngestLayer(self, "Ingest", lake_bucket=self.data.lake_bucket, ephemeral=ephemeral)

        # Detection → alerting. The archive lets us replay a demo's events.
        self.bus = events.EventBus(self, "Bus", event_bus_name=f"railpulse-{stage}")
        self.bus.archive(
            "Archive",
            event_pattern=events.EventPattern(source=["railpulse.detection"]),
            retention=Duration.days(7),
        )

        # Every platform alarm in every stack notifies this topic (track incidents go to the
        # escalation ladder instead — this is for whoever keeps RailPulse itself running).
        self.ops_topic = sns.Topic(self, "OpsTopic", display_name="RailPulse ops alarms", enforce_ssl=True)
        if ops_email:
            self.ops_topic.add_subscription(subs.EmailSubscription(ops_email))

        self.alarm(
            "DlqNotEmpty",
            "Telemetry messages failed processing 5 times — inspect the DLQ",
            self.ingest.dlq.metric_approximate_number_of_messages_visible(period=Duration.minutes(1)),
            threshold=0,
        )
        self.alarm(
            "TelemetryQueueStale",
            "Oldest telemetry message > 5 min — detection is falling behind",
            self.ingest.queue.metric_approximate_age_of_oldest_message(period=Duration.minutes(1)),
            threshold=300,
            evaluation_periods=3,
        )

        # Output keys are read by simulator/provision.py and simulator/seed_assets.py.
        outputs = {
            "TelemetryQueueUrl": self.ingest.queue.queue_url,
            "TelemetryDlqUrl": self.ingest.dlq.queue_url,
            "DeliveryStreamName": self.ingest.delivery_stream.ref,
            "LakeBucketName": self.data.lake_bucket.bucket_name,
            "TelemetryTableName": self.data.telemetry.table_name,
            "IncidentsTableName": self.data.incidents.table_name,
            "AssetsTableName": self.data.assets.table_name,
            "BaselinesTableName": self.data.baselines.table_name,
            "SensorPolicyName": self.ingest.sensor_policy.ref,
            "SimulatorPolicyName": self.ingest.simulator_policy.ref,
            "EventBusName": self.bus.event_bus_name,
            "OpsTopicArn": self.ops_topic.topic_arn,
            "VpcId": self.network.vpc.vpc_id,
        }
        for key, value in outputs.items():
            CfnOutput(self, key, value=value)

    def alarm(self, construct_id: str, description: str, metric: cloudwatch.IMetric, *, threshold: float,
              evaluation_periods: int = 1, scope: Construct | None = None,
              comparison=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD) -> cloudwatch.Alarm:
        """An alarm wired to the ops topic. `scope` lets other stacks create alarms in themselves."""
        alarm = cloudwatch.Alarm(
            scope or self, construct_id,
            alarm_description=description,
            metric=metric,
            threshold=threshold,
            comparison_operator=comparison,
            evaluation_periods=evaluation_periods,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        action = cw_actions.SnsAction(self.ops_topic)
        alarm.add_alarm_action(action)
        alarm.add_ok_action(action)
        return alarm
