from aws_cdk import Duration, Stack
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_cloudwatch as cw
from constructs import Construct

from .alerting_stack import RailPulseAlertingStack
from .core_stack import RailPulseCoreStack


class RailPulseOpsStack(Stack):
    """One CloudWatch dashboard for the demo's "does it scale?" question, plus a cost budget."""

    def __init__(self, scope: Construct, construct_id: str, *, stage: str, core: RailPulseCoreStack,
                 alerting: RailPulseAlertingStack, budget_usd: int = 50, budget_email: str | None = None,
                 **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        q, dlq = core.ingest.queue, core.ingest.dlq
        one_min = Duration.minutes(1)

        def eb_health(env: str) -> cw.Metric:
            return cw.Metric(namespace="AWS/ElasticBeanstalk", metric_name="EnvironmentHealth",
                             dimensions_map={"EnvironmentName": f"railpulse-{stage}-{env}"},
                             statistic="Maximum", period=one_min, label=env)

        def eb_instances(env: str) -> cw.Metric:
            # Published by enhanced health: instances currently reporting OK.
            return cw.Metric(namespace="AWS/ElasticBeanstalk", metric_name="InstancesOk",
                             dimensions_map={"EnvironmentName": f"railpulse-{stage}-{env}"},
                             statistic="Maximum", period=one_min, label=f"{env} instances OK")

        dash = cw.Dashboard(self, "Dashboard", dashboard_name=f"railpulse-{stage}",
                            default_interval=Duration.hours(3))
        dash.add_widgets(
            cw.TextWidget(markdown=(
                f"# RailPulse {stage}\nIngest → detection → escalation. Burst test: "
                "`python -m railpulse_sim --burst-at 30 --burst-for 300` and watch the queue fill, "
                "workers scale out, and the backlog drain."), width=24, height=2),
        )
        dash.add_widgets(
            cw.GraphWidget(title="Telemetry queue: backlog vs. throughput", width=12, left=[
                q.metric_approximate_number_of_messages_visible(period=one_min, label="waiting"),
            ], right=[
                q.metric_number_of_messages_deleted(period=one_min, statistic="Sum", label="processed / min"),
            ]),
            cw.GraphWidget(title="Oldest waiting message (s) and dead letters", width=12, left=[
                q.metric_approximate_age_of_oldest_message(period=one_min, label="oldest message age"),
            ], right=[
                dlq.metric_approximate_number_of_messages_visible(period=one_min, label="DLQ"),
            ]),
        )
        dash.add_widgets(
            cw.GraphWidget(title="Instances reporting OK", width=12,
                           left=[eb_instances("worker"), eb_instances("web")]),
            cw.GraphWidget(title="Environment health (0 OK · 20 degraded · 25 severe)", width=12,
                           left=[eb_health("worker"), eb_health("web")],
                           left_annotations=[cw.HorizontalAnnotation(value=20, label="degraded")]),
        )
        sm = alerting.state_machine
        dash.add_widgets(
            cw.GraphWidget(title="Escalations", width=12, left=[
                sm.metric_started(period=Duration.minutes(5), statistic="Sum", label="started"),
                sm.metric_succeeded(period=Duration.minutes(5), statistic="Sum", label="acknowledged / superseded"),
                sm.metric_failed(period=Duration.minutes(5), statistic="Sum", label="nobody acknowledged"),
            ]),
            cw.GraphWidget(title="Alerting Lambda errors", width=12, left=[
                f.metric_errors(period=Duration.minutes(5), statistic="Sum", label=name)
                for name, f in alerting.functions.items()
            ]),
        )
        dash.add_widgets(
            cw.GraphWidget(title="DynamoDB writes (WCU / min)", width=24, left=[
                t.metric_consumed_write_capacity_units(period=one_min, statistic="Sum", label=name)
                for name, t in (("telemetry", core.data.telemetry), ("incidents", core.data.incidents),
                                ("baselines", core.data.baselines))
            ]),
        )

        if budget_email:
            budgets.CfnBudget(
                self, "MonthlyBudget",
                budget=budgets.CfnBudget.BudgetDataProperty(
                    budget_name=f"railpulse-{stage}-monthly",
                    budget_type="COST",
                    time_unit="MONTHLY",
                    budget_limit=budgets.CfnBudget.SpendProperty(amount=budget_usd, unit="USD"),
                    # Account-wide on purpose: a tag filter (project=railpulse) only works after the
                    # tag is activated as a cost-allocation tag in Billing, and reads $0 until then.
                ),
                notifications_with_subscribers=[
                    budgets.CfnBudget.NotificationWithSubscribersProperty(
                        notification=budgets.CfnBudget.NotificationProperty(
                            notification_type=kind, comparison_operator="GREATER_THAN",
                            threshold=pct, threshold_type="PERCENTAGE"),
                        subscribers=[budgets.CfnBudget.SubscriberProperty(subscription_type="EMAIL",
                                                                          address=budget_email)],
                    )
                    for kind, pct in (("ACTUAL", 80), ("FORECASTED", 100))
                ],
            )
