from pathlib import Path

from aws_cdk import CfnOutput, Duration, Stack
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_elasticbeanstalk as eb
from aws_cdk import aws_iam as iam
from constructs import Construct

from .core_stack import RailPulseCoreStack
from .eb_common import (
    PY_IGNORE,
    SHARED_EBEXTENSIONS,
    allow_self_imds_update,
    health_metrics_config,
    imds_options,
    source_bundle,
)

WORKER_DIR = Path(__file__).resolve().parents[2] / "worker"


def _opt(namespace: str, name: str, value) -> eb.CfnEnvironment.OptionSettingProperty:
    return eb.CfnEnvironment.OptionSettingProperty(namespace=namespace, option_name=name, value=str(value))


class RailPulseWorkerStack(Stack):
    """Elastic Beanstalk worker tier: sqsd pulls telemetry-q and POSTs to /process."""

    def __init__(self, scope: Construct, construct_id: str, *, stage: str, core: RailPulseCoreStack,
                 solution_stack: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        data, ingest = core.data, core.ingest

        # --- Source bundle: Dockerfile + app + own and shared .ebextensions ---------------------
        bundle = source_bundle(self, "Bundle", path=WORKER_DIR, copies=[
            (WORKER_DIR, ".", PY_IGNORE),
            (SHARED_EBEXTENSIONS, ".ebextensions", ()),
        ])

        app_name = f"railpulse-{stage}"
        self.application_name = app_name  # shared with the web environment (web_stack.py)
        app = eb.CfnApplication(self, "App", application_name=app_name,
                                description="RailPulse Ops track-health platform")
        version = eb.CfnApplicationVersion(
            self,
            "Version",
            application_name=app_name,
            source_bundle=eb.CfnApplicationVersion.SourceBundleProperty(
                s3_bucket=bundle.s3_bucket_name, s3_key=bundle.s3_object_key
            ),
        )
        version.node.add_dependency(app)

        # --- IAM -----------------------------------------------------------------------------
        instance_role = iam.Role(
            self,
            "InstanceRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                # sqsd (queue polling, leader election), log streaming, enhanced health.
                iam.ManagedPolicy.from_aws_managed_policy_name("AWSElasticBeanstalkWorkerTier"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AWSElasticBeanstalkWebTier"),
            ],
        )
        ingest.queue.grant_consume_messages(instance_role)
        data.telemetry.grant_write_data(instance_role)
        data.baselines.grant_read_write_data(instance_role)
        data.assets.grant_read_data(instance_role)
        data.incidents.grant_read_write_data(instance_role)
        core.bus.grant_put_events_to(instance_role)
        allow_self_imds_update(instance_role, f"railpulse-{stage}-worker")

        profile = iam.CfnInstanceProfile(self, "InstanceProfile", roles=[instance_role.role_name])

        service_role = iam.Role(
            self,
            "ServiceRole",
            assumed_by=iam.ServicePrincipal("elasticbeanstalk.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSElasticBeanstalkEnhancedHealth"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AWSElasticBeanstalkManagedUpdatesCustomerRolePolicy"),
            ],
        )

        # --- Environment -----------------------------------------------------------------------
        env_ns = "aws:elasticbeanstalk:application:environment"
        options = [
            _opt("aws:elasticbeanstalk:environment", "EnvironmentType", "LoadBalanced"),
            _opt("aws:elasticbeanstalk:environment", "ServiceRole", service_role.role_arn),
            _opt("aws:autoscaling:launchconfiguration", "IamInstanceProfile", profile.ref),
            *imds_options(),
            # Standard: private subnets, no public IPs. Lean: public subnets, no inbound rules
            # (a worker has no load balancer and needs none). See network.py.
            _opt("aws:ec2:vpc", "VPCId", core.network.vpc.vpc_id),
            _opt("aws:ec2:vpc", "Subnets", core.network.instance_subnet_ids),
            _opt("aws:ec2:vpc", "AssociatePublicIpAddress", core.network.instances_get_public_ip),
            _opt("aws:ec2:instances", "InstanceTypes", "t3.small"),
            _opt("aws:autoscaling:asg", "MinSize", 1),
            _opt("aws:autoscaling:asg", "MaxSize", 4),
            # Secondary signal; the primary SQS-backlog policy is in worker/.ebextensions.
            _opt("aws:autoscaling:trigger", "MeasureName", "CPUUtilization"),
            _opt("aws:autoscaling:trigger", "Statistic", "Average"),
            _opt("aws:autoscaling:trigger", "Unit", "Percent"),
            _opt("aws:autoscaling:trigger", "Period", 1),
            _opt("aws:autoscaling:trigger", "BreachDuration", 2),
            _opt("aws:autoscaling:trigger", "UpperThreshold", 70),
            _opt("aws:autoscaling:trigger", "LowerThreshold", 30),
            # sqsd
            _opt("aws:elasticbeanstalk:sqsd", "WorkerQueueURL", ingest.queue.queue_url),
            _opt("aws:elasticbeanstalk:sqsd", "HttpPath", "/process"),
            _opt("aws:elasticbeanstalk:sqsd", "MimeType", "application/json"),
            _opt("aws:elasticbeanstalk:sqsd", "HttpConnections", 20),
            _opt("aws:elasticbeanstalk:sqsd", "InactivityTimeout", 60),
            _opt("aws:elasticbeanstalk:sqsd", "VisibilityTimeout", 120),  # matches the queue
            _opt("aws:elasticbeanstalk:sqsd", "ErrorVisibilityTimeout", 10),
            # Deploys: queue buffers during rollout, extra batch keeps capacity constant.
            _opt("aws:elasticbeanstalk:command", "DeploymentPolicy", "RollingWithAdditionalBatch"),
            _opt("aws:elasticbeanstalk:command", "BatchSizeType", "Percentage"),
            _opt("aws:elasticbeanstalk:command", "BatchSize", 50),
            _opt("aws:elasticbeanstalk:healthreporting:system", "SystemType", "enhanced"),
            health_metrics_config(),
            _opt("aws:elasticbeanstalk:cloudwatch:logs", "StreamLogs", "true"),
            _opt("aws:elasticbeanstalk:cloudwatch:logs", "RetentionInDays", 14),
            _opt("aws:elasticbeanstalk:cloudwatch:logs", "DeleteOnTerminate", "false"),
            # App config (read by worker/app/config.py)
            _opt(env_ns, "RAILPULSE_REGION", self.region),
            _opt(env_ns, "TELEMETRY_TABLE", data.telemetry.table_name),
            _opt(env_ns, "INCIDENTS_TABLE", data.incidents.table_name),
            _opt(env_ns, "ASSETS_TABLE", data.assets.table_name),
            _opt(env_ns, "BASELINES_TABLE", data.baselines.table_name),
            _opt(env_ns, "EVENT_BUS_NAME", core.bus.event_bus_name),
            _opt(env_ns, "TELEMETRY_QUEUE_NAME", ingest.queue.queue_name),  # for .ebextensions alarms
        ]

        env = eb.CfnEnvironment(
            self,
            "WorkerEnv",
            application_name=app_name,
            environment_name=f"railpulse-{stage}-worker",
            solution_stack_name=solution_stack,
            tier=eb.CfnEnvironment.TierProperty(name="Worker", type="SQS/HTTP"),
            version_label=version.ref,
            option_settings=options,
        )
        env.node.add_dependency(app)
        env.node.add_dependency(core.network.vpc)  # NAT + endpoints up before instances boot

        # Enhanced health publishes EnvironmentHealth: 0 OK, 15 Warning, 20 Degraded, 25 Severe.
        core.alarm(
            "WorkerEnvDegraded",
            "Detection worker environment health is Degraded or worse",
            cloudwatch.Metric(namespace="AWS/ElasticBeanstalk", metric_name="EnvironmentHealth",
                              dimensions_map={"EnvironmentName": f"railpulse-{stage}-worker"},
                              statistic="Maximum", period=Duration.minutes(1)),
            threshold=20, evaluation_periods=5, scope=self,
            comparison=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        )

        CfnOutput(self, "ApplicationName", value=app_name)
        CfnOutput(self, "WorkerEnvironmentName", value=env.ref)
