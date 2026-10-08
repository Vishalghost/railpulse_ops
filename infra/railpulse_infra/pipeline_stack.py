"""Phase 6 CI/CD: GitHub → CodePipeline (V2) → CodeBuild test → [approval] → CodeBuild deploy.

Opt-in: only created when `-c github_repo=owner/name -c codeconnection_arn=…` are given.
The connection is an AWS CodeConnections GitHub connection, which has to be created and
authorised once in the console (the GitHub handshake can't be done from CloudFormation).
"""

from aws_cdk import Duration, Stack
from aws_cdk import aws_codebuild as codebuild
from aws_cdk import aws_codepipeline as codepipeline
from aws_cdk import aws_codepipeline_actions as actions
from aws_cdk import aws_codestarnotifications as notifications
from aws_cdk import aws_iam as iam
from aws_cdk import aws_sns as sns
from constructs import Construct


class RailPulsePipelineStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, stage: str, github_repo: str, branch: str,
                 connection_arn: str, ops_topic: sns.ITopic, ops_email: str | None = None,
                 require_approval: bool = False, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        owner, repo = github_repo.split("/", 1)

        env_vars = {
            "STAGE": stage,
            # Passed back into `cdk deploy` so the pipeline keeps managing itself.
            "GITHUB_REPO": github_repo,
            "CODECONNECTION_ARN": connection_arn,
        }
        if ops_email:
            env_vars["OPS_EMAIL"] = ops_email
        project = codebuild.PipelineProject(
            self, "Build",
            project_name=f"railpulse-{stage}-build",
            build_spec=codebuild.BuildSpec.from_source_filename("buildspec.yml"),
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0,  # Ubuntu 22.04: Python 3.12, Node 20
                compute_type=codebuild.ComputeType.SMALL,
            ),
            environment_variables={k: codebuild.BuildEnvironmentVariable(value=v) for k, v in env_vars.items()},
            cache=codebuild.Cache.local(codebuild.LocalCacheMode.CUSTOM),
            timeout=Duration.minutes(45),
        )
        # `cdk deploy` works through the bootstrap roles (cdk-…-deploy/file-publishing/lookup),
        # so the build itself needs nothing broader than permission to assume them.
        project.add_to_role_policy(iam.PolicyStatement(
            actions=["sts:AssumeRole"],
            resources=[f"arn:{self.partition}:iam::{self.account}:role/cdk-*"],
        ))

        source = codepipeline.Artifact("Source")
        stages = [
            codepipeline.StageProps(stage_name="Source", actions=[
                actions.CodeStarConnectionsSourceAction(
                    action_name="GitHub", owner=owner, repo=repo, branch=branch,
                    connection_arn=connection_arn, output=source),
            ]),
            codepipeline.StageProps(stage_name="Test", actions=[
                actions.CodeBuildAction(action_name="TestAndSynth", project=project, input=source),
            ]),
        ]
        if require_approval:
            stages.append(codepipeline.StageProps(stage_name="Approve", actions=[
                actions.ManualApprovalAction(action_name="Approve", notification_topic=ops_topic,
                                             additional_information=f"Deploy RailPulse {stage}?"),
            ]))
        stages.append(codepipeline.StageProps(stage_name="Deploy", actions=[
            actions.CodeBuildAction(
                action_name="CdkDeploy", project=project, input=source,
                environment_variables={"RAILPULSE_DEPLOY": codebuild.BuildEnvironmentVariable(value="true")}),
        ]))

        self.pipeline = codepipeline.Pipeline(
            self, "Pipeline",
            pipeline_name=f"railpulse-{stage}",
            pipeline_type=codepipeline.PipelineType.V2,  # billed per action-minute, not per month
            restart_execution_on_update=False,
            stages=stages,
        )
        notifications.NotificationRule(
            self, "PipelineFailed",
            source=self.pipeline,
            events=["codepipeline-pipeline-pipeline-execution-failed"],
            targets=[ops_topic],
            detail_type=notifications.DetailType.BASIC,
        )
