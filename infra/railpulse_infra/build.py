import os
from dataclasses import dataclass

import aws_cdk as cdk

from .alerting_stack import RailPulseAlertingStack
from .analytics_stack import RailPulseAnalyticsStack
from .core_stack import RailPulseCoreStack
from .edge_stack import RailPulseEdgeStack
from .ops_stack import RailPulseOpsStack
from .pipeline_stack import RailPulsePipelineStack
from .web_stack import RailPulseWebStack
from .worker_stack import RailPulseWorkerStack


@dataclass
class RailPulseApp:
    app: cdk.App
    core: RailPulseCoreStack
    worker: RailPulseWorkerStack
    alerting: RailPulseAlertingStack
    edge: RailPulseEdgeStack
    web: RailPulseWebStack
    ops: RailPulseOpsStack
    analytics: RailPulseAnalyticsStack
    pipeline: RailPulsePipelineStack | None


def build_app(app: cdk.App, account: str | None = None, region: str | None = None) -> RailPulseApp:
    ctx = app.node.try_get_context
    stage = ctx("stage") or "dev"
    account = account or os.environ.get("CDK_DEFAULT_ACCOUNT")

    # Mumbai by default: keeps railway telemetry inside India.
    env = cdk.Environment(account=account, region=region or os.environ.get("RAILPULSE_REGION", "ap-south-1"))
    # CloudFront-scoped WAF can only exist in us-east-1.
    edge_env = cdk.Environment(account=account, region="us-east-1")

    # Lean (default for every stage except prod): no NAT gateway / interface endpoints, so the
    # always-on bill fits a small budget. `-c lean=false` gives dev the full private network.
    lean_ctx = ctx("lean")
    lean = (stage != "prod") if lean_ctx is None else str(lean_ctx).lower() in ("1", "true", "yes")
    core = RailPulseCoreStack(
        app, f"RailPulse-{stage}-Core",
        stage=stage,
        ops_email=ctx("ops_email") or None,
        lean=lean,
        nat_gateways=int(ctx("nat_gateways") or (2 if stage == "prod" else 1)),
        env=env,
    )

    # Refresh with: aws elasticbeanstalk list-available-solution-stacks --query
    #   "SolutionStacks[?contains(@,'Amazon Linux 2023') && contains(@,'running Docker')]"
    solution_stack = ctx("eb_solution_stack")

    worker = RailPulseWorkerStack(
        app, f"RailPulse-{stage}-Worker",
        stage=stage, core=core, solution_stack=solution_stack, env=env,
    )
    alerting = RailPulseAlertingStack(
        app, f"RailPulse-{stage}-Alerting",
        stage=stage,
        core=core,
        # cdk deploy -c sms_mode=dry_run -c alert_email=you@example.com -c ack_timeout_override_s=60
        sms_mode=ctx("sms_mode") or "off",
        alert_email=ctx("alert_email") or None,
        ack_timeout_override_s=int(ctx("ack_timeout_override_s") or 0) or None,
        silent_after_s=int(ctx("silent_after_s") or 900),
        env=env,
    )
    edge = RailPulseEdgeStack(
        app, f"RailPulse-{stage}-Edge",
        stage=stage, env=edge_env, cross_region_references=True,
    )
    web = RailPulseWebStack(
        app, f"RailPulse-{stage}-Web",
        stage=stage,
        core=core,
        alerting=alerting,  # ack Function URL becomes a CloudFront origin
        application_name=worker.application_name,  # one EB application, two environments
        solution_stack=solution_stack,
        web_acl_arn=edge.web_acl.attr_arn,
        env=env,
        cross_region_references=True,
    )
    web.node.add_dependency(worker)
    ops = RailPulseOpsStack(
        app, f"RailPulse-{stage}-Ops",
        stage=stage,
        core=core,
        alerting=alerting,
        budget_usd=int(ctx("budget_usd") or 50),
        budget_email=ctx("ops_email") or None,
        env=env,
    )

    analytics = RailPulseAnalyticsStack(app, f"RailPulse-{stage}-Analytics", stage=stage, core=core, env=env)

    pipeline = None
    if ctx("github_repo") and ctx("codeconnection_arn"):  # CI/CD is opt-in
        pipeline = RailPulsePipelineStack(
            app, f"RailPulse-{stage}-Pipeline",
            stage=stage,
            github_repo=ctx("github_repo"),
            branch=ctx("github_branch") or "main",
            connection_arn=ctx("codeconnection_arn"),
            ops_topic=core.ops_topic,
            ops_email=ctx("ops_email") or None,
            require_approval=stage == "prod",
            env=env,
        )

    cdk.Tags.of(app).add("project", "railpulse")
    cdk.Tags.of(app).add("stage", stage)
    return RailPulseApp(app, core, worker, alerting, edge, web, ops, analytics, pipeline)
