"""Assertions on the synthesized CloudFormation — the security/scaling properties Phase 5
promises, checked without deploying anything."""

import json

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from railpulse_infra.build import build_app

ACCOUNT, REGION = "123456789012", "ap-south-1"


STACKS = ("core", "worker", "alerting", "edge", "web", "ops")


def _templates(**context) -> dict[str, Template]:
    cfg = json.load(open("cdk.json"))
    rp = build_app(cdk.App(context={**cfg["context"], "ops_email": "ops@example.com", **context}),
                   account=ACCOUNT, region=REGION)
    return {name: Template.from_stack(getattr(rp, name)) for name in STACKS}


@pytest.fixture(scope="module")
def t():
    return _templates()  # dev → lean by default


@pytest.fixture(scope="module")
def t_prod():
    return _templates(stage="prod")


def options(template: Template, env_logical_prefix: str) -> dict[tuple[str, str], object]:
    envs = template.find_resources("AWS::ElasticBeanstalk::Environment")
    [(_, env)] = [(k, v) for k, v in envs.items() if k.startswith(env_logical_prefix)]
    return {(o["Namespace"], o["OptionName"]): o["Value"] for o in env["Properties"]["OptionSettings"]}


# --- encryption ---------------------------------------------------------------------------

def test_tables_use_customer_managed_kms(t):
    tables = t["core"].find_resources("AWS::DynamoDB::GlobalTable")
    assert len(tables) == 4
    for table in tables.values():
        assert table["Properties"]["SSESpecification"]["SSEType"] == "KMS"


def test_key_rotates_and_lake_uses_it(t):
    t["core"].has_resource_properties("AWS::KMS::Key", {"EnableKeyRotation": True})
    t["core"].has_resource_properties("AWS::S3::Bucket", {"BucketEncryption": {"ServerSideEncryptionConfiguration": [
        Match.object_like({"ServerSideEncryptionByDefault": Match.object_like({"SSEAlgorithm": "aws:kms"}),
                           "BucketKeyEnabled": True})]}})


# --- network ------------------------------------------------------------------------------

def _endpoint_kinds(template: Template) -> str:
    return " ".join(json.dumps(e["Properties"]["ServiceName"])
                    for e in template.find_resources("AWS::EC2::VPCEndpoint").values())


def test_dev_is_lean_no_nat_no_interface_endpoints(t):
    t["core"].resource_count_is("AWS::EC2::NatGateway", 0)
    kinds = _endpoint_kinds(t["core"])
    assert "dynamodb" in kinds and ".s3" in kinds  # free gateway endpoints stay
    assert "sqs" not in kinds                       # hourly-billed interface endpoint goes


def test_prod_is_private_with_nat_per_az_and_sqs_endpoint(t_prod):
    t_prod["core"].resource_count_is("AWS::EC2::NatGateway", 2)
    assert "sqs" in _endpoint_kinds(t_prod["core"])
    for stack, prefix in (("worker", "WorkerEnv"), ("web", "WebEnv")):
        assert options(t_prod[stack], prefix)[("aws:ec2:vpc", "AssociatePublicIpAddress")] == "false"


@pytest.mark.parametrize("stack,prefix", [("worker", "WorkerEnv"), ("web", "WebEnv")])
def test_instances_imdsv2_only_and_may_only_fix_own_hop_limit(t, stack, prefix):
    opts = options(t[stack], prefix)
    assert opts[("aws:autoscaling:launchconfiguration", "DisableIMDSv1")] == "true"
    env_name = f"railpulse-dev-{'worker' if stack == 'worker' else 'web'}"
    t[stack].has_resource_properties("AWS::IAM::Policy", {"PolicyDocument": {"Statement": Match.array_with([
        Match.object_like({"Action": "ec2:ModifyInstanceMetadataOptions",
                           "Condition": {"StringEquals": {"ec2:ResourceTag/elasticbeanstalk:environment-name": env_name}}}),
    ])}})


def test_budget_defaults_to_50_usd(t):
    t["ops"].has_resource_properties("AWS::Budgets::Budget", {
        "Budget": Match.object_like({"BudgetLimit": {"Amount": 50, "Unit": "USD"}})})


# --- edge / origin lock ---------------------------------------------------------------------

def test_waf_has_managed_rules_and_api_rate_limit(t):
    t["edge"].has_resource_properties("AWS::WAFv2::WebACL", {
        "Scope": "CLOUDFRONT",
        "Rules": Match.array_with([  # (array_with is order-sensitive: template order = priority)
            Match.object_like({"Name": "AWSManagedRulesAmazonIpReputationList"}),
            Match.object_like({"Name": "AWSManagedRulesCommonRuleSet"}),
            Match.object_like({"Name": "AWSManagedRulesKnownBadInputsRuleSet"}),
            Match.object_like({"Name": "ApiRateLimit", "Action": {"Block": {}}}),
            Match.object_like({"Name": "AckRateLimit", "Action": {"Block": {}}}),
        ]),
    })


def test_ack_links_served_through_cloudfront_and_locked(t):
    dist = next(iter(t["web"].find_resources("AWS::CloudFront::Distribution").values()))
    cfg = dist["Properties"]["DistributionConfig"]
    [ack] = [b for b in cfg["CacheBehaviors"] if b["PathPattern"] == "/ack*"]
    assert ack["ViewerProtocolPolicy"] == "https-only"
    ack_origin = next(o for o in cfg["Origins"] if o["Id"] == ack["TargetOriginId"])
    assert ack_origin["OriginCustomHeaders"][0]["HeaderName"] == "X-Origin-Verify"
    # The Lambda enforces the header; it reads the secret by name at runtime.
    t["alerting"].has_resource_properties("AWS::Lambda::Function", {"Environment": {"Variables": Match.object_like({
        "REQUIRE_ORIGIN_VERIFY": "true", "ORIGIN_VERIFY_SECRET_ID": "railpulse/dev/origin-verify"})}})
    t["web"].has_resource_properties("AWS::SecretsManager::Secret", {"Name": "railpulse/dev/origin-verify"})


def test_shared_imds_hop_limit_config_exists():
    # Bundle *contents* are produced by local bundling during `cdk synth`; README "Verifying"
    # and the Phase 6 CI build list the assembled bundles. Here: the shared source is present.
    from railpulse_infra.eb_common import SHARED_EBEXTENSIONS
    cfg = (SHARED_EBEXTENSIONS / "02-imds-hop-limit.config").read_text()
    assert "--http-put-response-hop-limit 2" in cfg


def test_cloudfront_attaches_waf_and_sends_origin_secret(t):
    dist = next(iter(t["web"].find_resources("AWS::CloudFront::Distribution").values()))
    cfg = dist["Properties"]["DistributionConfig"]
    assert "WebACLId" in cfg
    headers = cfg["Origins"][0]["OriginCustomHeaders"]
    assert headers[0]["HeaderName"] == "X-Origin-Verify"
    assert "resolve:secretsmanager" in json.dumps(headers[0]["HeaderValue"])  # dynamic ref, not plaintext


def test_alb_only_admits_cloudfront(t):
    sgs = t["web"].find_resources("AWS::EC2::SecurityGroup")
    [(alb_id, alb)] = [(k, sg) for k, sg in sgs.items() if k.startswith("AlbSecurityGroup")]
    assert "SecurityGroupIngress" not in alb["Properties"]  # no inline (e.g. 0.0.0.0/0) rules
    # The prefix-list ID is only known at deploy time, so CDK emits a standalone ingress resource.
    ingress = [r["Properties"] for r in t["web"].find_resources("AWS::EC2::SecurityGroupIngress").values()
               if r["Properties"]["GroupId"] == {"Fn::GetAtt": [alb_id, "GroupId"]}]
    assert len(ingress) == 1
    [rule] = ingress
    assert "SourcePrefixListId" in rule and "CidrIp" not in rule
    assert (rule["FromPort"], rule["ToPort"]) == (80, 80)
    opts = options(t["web"], "WebEnv")
    assert ("aws:elbv2:loadbalancer", "ManagedSecurityGroup") in opts
    assert "resolve:secretsmanager" in json.dumps(opts[("aws:elasticbeanstalk:application:environment",
                                                        "ORIGIN_VERIFY_SECRET")])


# --- scaling --------------------------------------------------------------------------------

def test_web_scales_on_cpu_70(t):
    opts = options(t["web"], "WebEnv")
    assert opts[("aws:autoscaling:trigger", "MeasureName")] == "CPUUtilization"
    assert opts[("aws:autoscaling:trigger", "UpperThreshold")] == "70"
    assert opts[("aws:elasticbeanstalk:command", "DeploymentPolicy")] == "RollingWithAdditionalBatch"


def test_health_metrics_published_for_dashboard(t):
    doc = json.loads(options(t["web"], "WebEnv")[("aws:elasticbeanstalk:healthreporting:system", "ConfigDocument")])
    assert {"InstancesOk", "ApplicationRequests5xx"} <= doc["CloudWatchMetrics"]["Environment"].keys()


# --- ops ------------------------------------------------------------------------------------

def test_every_alarm_notifies_ops(t):
    for name in ("core", "worker", "alerting", "web"):
        alarms = t[name].find_resources("AWS::CloudWatch::Alarm")
        assert alarms, f"{name} has no alarms"
        for logical_id, alarm in alarms.items():
            if logical_id.startswith("QueueBacklog"):  # (ebextensions alarms aren't in CDK)
                continue
            assert alarm["Properties"].get("AlarmActions"), f"{name}/{logical_id} notifies nobody"


def test_heartbeat_runs_every_five_minutes(t):
    t["alerting"].has_resource_properties("AWS::Scheduler::Schedule", {"ScheduleExpression": "rate(5 minutes)"})


def test_ops_dashboard_and_budget(t):
    t["ops"].resource_count_is("AWS::CloudWatch::Dashboard", 1)
    t["ops"].has_resource_properties("AWS::Budgets::Budget", {
        "Budget": Match.object_like({"BudgetType": "COST", "TimeUnit": "MONTHLY"})})
    t["core"].has_resource_properties("AWS::SNS::Subscription", {"Protocol": "email", "Endpoint": "ops@example.com"})
