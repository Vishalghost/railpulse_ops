from pathlib import Path

from aws_cdk import ArnFormat, CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_sns as sns
from aws_cdk import aws_sns_subscriptions as subs
from aws_cdk import aws_sqs as sqs
from aws_cdk import aws_stepfunctions as sfn
from constructs import Construct

from .core_stack import RailPulseCoreStack
from .names import ack_base_url_param, origin_secret_name

ALERTING_DIR = Path(__file__).resolve().parents[2] / "alerting"


class RailPulseAlertingStack(Stack):
    """AnomalyDetected → Step Functions escalation → Translate + Polly + SMS/SNS → ack page."""

    def __init__(self, scope: Construct, construct_id: str, *, stage: str, core: RailPulseCoreStack,
                 sms_mode: str = "off", alert_email: str | None = None,
                 ack_timeout_override_s: int | None = None, silent_after_s: int = 900, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        data = core.data
        ephemeral = stage != "prod"
        removal = RemovalPolicy.DESTROY if ephemeral else RemovalPolicy.RETAIN

        code = lambda_.Code.from_asset(
            str(ALERTING_DIR), exclude=["tests", "**/__pycache__", ".pytest_cache", "pytest.ini", "statemachine"]
        )

        def fn(name: str, handler: str, env: dict, timeout_s: int = 30) -> lambda_.Function:
            return lambda_.Function(
                self, name,
                runtime=lambda_.Runtime.PYTHON_3_12,
                architecture=lambda_.Architecture.ARM_64,
                code=code,
                handler=f"railpulse_alerting.handlers.{handler}",
                timeout=Duration.seconds(timeout_s),
                memory_size=256,
                environment=env,
                tracing=lambda_.Tracing.ACTIVE,
                log_group=logs.LogGroup(self, f"{name}Logs", retention=logs.RetentionDays.TWO_WEEKS,
                                        removal_policy=removal),
            )

        # Fixed name so the ack Lambda's IAM policy can reference the ARN without a
        # dependency cycle (ack → state machine → notify → ack URL).
        sm_name = f"railpulse-{stage}-escalation"
        sm_arn = self.format_arn(service="states", resource="stateMachine", resource_name=sm_name,
                                 arn_format=ArnFormat.COLON_RESOURCE_NAME)

        # --- Ack page -------------------------------------------------------------------------
        # Served through CloudFront + WAF at <dashboard>/ack/ (web stack). The Function URL is
        # public at the HTTP layer but the handler refuses requests lacking the origin secret.
        secret_arn = (f"arn:{self.partition}:secretsmanager:{self.region}:{self.account}"
                      f":secret:{origin_secret_name(stage)}-*")  # Secrets Manager appends a random suffix
        ack_fn = fn("AckFunction", "ack.handler", {
            "INCIDENTS_TABLE": data.incidents.table_name,
            "VOICE_BUCKET": data.lake_bucket.bucket_name,
            "ORIGIN_VERIFY_SECRET_ID": origin_secret_name(stage),
            "REQUIRE_ORIGIN_VERIFY": "true",
        })
        data.incidents.grant_read_write_data(ack_fn)
        data.lake_bucket.grant_read(ack_fn, "voice/*")
        ack_fn.add_to_role_policy(iam.PolicyStatement(actions=["states:SendTaskSuccess"], resources=[sm_arn]))
        ack_fn.add_to_role_policy(iam.PolicyStatement(actions=["secretsmanager:GetSecretValue"], resources=[secret_arn]))
        ack_url = ack_fn.add_function_url(auth_type=lambda_.FunctionUrlAuthType.NONE)
        self.ack_url = ack_url  # CloudFront origin (web stack)

        # --- Notify (one rung of the ladder) ---------------------------------------------------
        self.alerts_topic = sns.Topic(self, "AlertsTopic", display_name="RailPulse alerts")
        if alert_email:
            self.alerts_topic.add_subscription(subs.EmailSubscription(alert_email))

        notify_fn = fn("NotifyFunction", "notify.handler", {
            "INCIDENTS_TABLE": data.incidents.table_name,
            "VOICE_BUCKET": data.lake_bucket.bucket_name,
            "ALERTS_TOPIC_ARN": self.alerts_topic.topic_arn,
            # CloudFront /ack/ URL, written to SSM by the web stack (name, not reference: no cycle).
            "ACK_BASE_URL_PARAM": ack_base_url_param(stage),
            "SMS_MODE": sms_mode,  # off | dry_run | live
            # Fill these once End User Messaging + TRAI DLT registration is done:
            "SMS_ORIGINATION_ID": "",
            "IN_DLT_ENTITY_ID": "",
            "IN_DLT_TEMPLATE_ID": "",
        }, timeout_s=60)
        data.incidents.grant_read_write_data(notify_fn)
        data.lake_bucket.grant_put(notify_fn, "voice/*")
        notify_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["ssm:GetParameter"],
            resources=[f"arn:{self.partition}:ssm:{self.region}:{self.account}:parameter{ack_base_url_param(stage)}"]))
        self.alerts_topic.grant_publish(notify_fn)
        notify_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["translate:TranslateText", "polly:SynthesizeSpeech", "sms-voice:SendTextMessage"],
            resources=["*"],
        ))

        # --- Escalation state machine --------------------------------------------------------
        self.state_machine = sfn.StateMachine(
            self, "Escalation",
            state_machine_name=sm_name,
            state_machine_type=sfn.StateMachineType.STANDARD,  # waits can last hours
            definition_body=sfn.DefinitionBody.from_file(str(ALERTING_DIR / "statemachine" / "escalation.asl.json")),
            definition_substitutions={
                "IncidentsTable": data.incidents.table_name,
                "NotifyFunctionArn": notify_fn.function_arn,
            },
            tracing_enabled=True,
            logs=sfn.LogOptions(
                destination=logs.LogGroup(self, "EscalationLogs", retention=logs.RetentionDays.TWO_WEEKS,
                                          removal_policy=removal),
                level=sfn.LogLevel.ERROR,
            ),
        )
        notify_fn.grant_invoke(self.state_machine)
        data.incidents.grant_read_write_data(self.state_machine)

        # --- EventBridge → start ---------------------------------------------------------------
        start_fn = fn("StartFunction", "start.handler", {
            "ASSETS_TABLE": data.assets.table_name,
            "STATE_MACHINE_ARN": sm_arn,
            "ACK_TIMEOUT_OVERRIDE_S": str(ack_timeout_override_s or ""),
        })
        data.assets.grant_read_data(start_fn)
        self.state_machine.grant_start_execution(start_fn)

        rule_dlq = sqs.Queue(self, "AlertRuleDlq", retention_period=Duration.days(14),
                             encryption=sqs.QueueEncryption.SQS_MANAGED, enforce_ssl=True)
        events.Rule(
            self, "IncidentToEscalation",
            event_bus=core.bus,
            event_pattern=events.EventPattern(
                source=["railpulse.detection"],
                detail_type=["AnomalyDetected", "IncidentEscalated"],
                detail={"severity": [{"numeric": ["<=", 3]}]},  # P4 is dashboard-only
            ),
            targets=[targets.LambdaFunction(start_fn, retry_attempts=8, max_event_age=Duration.hours(2),
                                            dead_letter_queue=rule_dlq)],
        )

        # --- Heartbeat: silence becomes an incident -------------------------------------------
        heartbeat_fn = fn("HeartbeatFunction", "heartbeat.handler", {
            "ASSETS_TABLE": data.assets.table_name,
            "TELEMETRY_TABLE": data.telemetry.table_name,
            "TELEMETRY_QUEUE_URL": core.ingest.queue.queue_url,
            "SILENT_AFTER_S": str(silent_after_s),  # -c silent_after_s=120 for demos
        }, timeout_s=120)
        data.assets.grant_read_write_data(heartbeat_fn)  # reads sensors, writes DIV#/HEALTH
        data.telemetry.grant_read_data(heartbeat_fn)
        core.ingest.queue.grant_send_messages(heartbeat_fn)

        scheduler_role = iam.Role(self, "SchedulerRole", assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"))
        heartbeat_fn.grant_invoke(scheduler_role)
        scheduler.CfnSchedule(
            self, "HeartbeatSchedule",
            name=f"railpulse-{stage}-heartbeat",
            description="Find trackside sensors that stopped reporting",
            schedule_expression="rate(5 minutes)",
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
            target=scheduler.CfnSchedule.TargetProperty(
                arn=heartbeat_fn.function_arn,
                role_arn=scheduler_role.role_arn,
                retry_policy=scheduler.CfnSchedule.RetryPolicyProperty(maximum_retry_attempts=2,
                                                                       maximum_event_age_in_seconds=240),
            ),
        )

        # --- Platform alarms → ops topic --------------------------------------------------------
        core.alarm(
            "EscalationUnacknowledged",
            "An escalation reached the end of its ladder with nobody acknowledging — call the DEN",
            self.state_machine.metric_failed(period=Duration.minutes(1), statistic="Sum"),
            threshold=0, scope=self,
        )
        core.alarm(
            "EscalationThrottled",
            "Escalation executions are being throttled — pages may be delayed",
            self.state_machine.metric_throttled(period=Duration.minutes(5), statistic="Sum"),
            threshold=0, scope=self,
        )
        core.alarm(
            "AlertRuleDlqNotEmpty",
            "Incident events could not be delivered to the escalation starter after 8 retries",
            rule_dlq.metric_approximate_number_of_messages_visible(period=Duration.minutes(1)),
            threshold=0, scope=self,
        )
        for name, f in (("Start", start_fn), ("Notify", notify_fn), ("Ack", ack_fn), ("Heartbeat", heartbeat_fn)):
            core.alarm(
                f"{name}FunctionErrors",
                f"{name} Lambda is failing",
                f.metric_errors(period=Duration.minutes(5), statistic="Sum"),
                threshold=0, scope=self,
            )

        self.functions = {"start": start_fn, "notify": notify_fn, "ack": ack_fn, "heartbeat": heartbeat_fn}

        CfnOutput(self, "AckUrl", value=ack_url.url)
        CfnOutput(self, "AlertsTopicArn", value=self.alerts_topic.topic_arn)
        CfnOutput(self, "EscalationStateMachineArn", value=self.state_machine.state_machine_arn)
