import json
from pathlib import Path

import aws_cdk as cdk
import pytest
import yaml
from aws_cdk.assertions import Match, Template

from railpulse_infra.build import build_app

ROOT = Path(__file__).resolve().parents[2]
CONN = "arn:aws:codeconnections:ap-south-1:123456789012:connection/abc"


def _rp(**context):
    cfg = json.load(open("cdk.json"))
    return build_app(cdk.App(context={**cfg["context"], **context}), account="123456789012", region="ap-south-1")


def test_pipeline_is_opt_in():
    assert _rp().pipeline is None


@pytest.fixture(scope="module")
def dev():
    return Template.from_stack(_rp(github_repo="me/railpulse", codeconnection_arn=CONN).pipeline)


def stage_names(t: Template) -> list[str]:
    [p] = t.find_resources("AWS::CodePipeline::Pipeline").values()
    return [s["Name"] for s in p["Properties"]["Stages"]]


def test_dev_pipeline_tests_then_deploys(dev):
    dev.has_resource_properties("AWS::CodePipeline::Pipeline", {"PipelineType": "V2"})
    assert stage_names(dev) == ["Source", "Test", "Deploy"]
    dev.has_resource_properties("AWS::CodeBuild::Project", {"Environment": Match.object_like({
        "Image": "aws/codebuild/standard:7.0", "ComputeType": "BUILD_GENERAL1_SMALL"})})


def test_build_can_only_assume_cdk_bootstrap_roles(dev):
    dev.has_resource_properties("AWS::IAM::Policy", {"PolicyDocument": {"Statement": Match.array_with([
        Match.object_like({"Action": "sts:AssumeRole",
                           "Resource": Match.object_like({"Fn::Join": Match.any_value()})})])}})
    policies = json.dumps(dev.find_resources("AWS::IAM::Policy"))
    assert ":role/cdk-*" in policies
    assert '"Action": "*"' not in policies


def test_failed_runs_notify_ops(dev):
    dev.has_resource_properties("AWS::CodeStarNotifications::NotificationRule", {
        "EventTypeIds": ["codepipeline-pipeline-pipeline-execution-failed"]})


def test_prod_pipeline_requires_manual_approval():
    t = Template.from_stack(_rp(stage="prod", github_repo="me/railpulse", codeconnection_arn=CONN).pipeline)
    assert stage_names(t) == ["Source", "Test", "Approve", "Deploy"]


def test_buildspec_is_valid_and_runs_every_suite():
    spec = yaml.safe_load((ROOT / "buildspec.yml").read_text())
    assert spec["version"] == 0.2
    build = "\n".join(spec["phases"]["build"]["commands"])
    for suite in ("simulator", "worker", "alerting", "api", "infra"):
        assert suite in build and (ROOT / suite / "pytest.ini").is_file()
    install = " ".join(spec["phases"]["install"]["commands"])
    for req in ("infra/requirements-dev.txt", "simulator/requirements.txt", "worker/requirements-dev.txt",
                "alerting/requirements-dev.txt", "api/requirements-dev.txt"):
        assert req in install and (ROOT / req).is_file(), req
    assert (ROOT / "dashboard" / "package-lock.json").is_file()  # `npm ci` needs it
