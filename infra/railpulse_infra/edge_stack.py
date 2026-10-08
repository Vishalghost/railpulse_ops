from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_wafv2 as wafv2
from constructs import Construct

# Per-IP request budget for /api/* over WAF's 5-minute window. Railway offices sit behind
# shared NAT, so this is sized for a whole control room, not one browser (~10 req/s sustained).
API_RATE_LIMIT_PER_5MIN = 3000
# A gangman opens one or two ack links per incident; anything near this is scanning for codes.
ACK_RATE_LIMIT_PER_5MIN = 100  # WAF's minimum rate limit is 10


def _rate_rule(name: str, priority: int, path_prefix: str, limit: int) -> wafv2.CfnWebACL.RuleProperty:
    return wafv2.CfnWebACL.RuleProperty(
        name=name,
        priority=priority,
        action=wafv2.CfnWebACL.RuleActionProperty(block={}),
        statement=wafv2.CfnWebACL.StatementProperty(
            rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                limit=limit,
                aggregate_key_type="IP",
                scope_down_statement=wafv2.CfnWebACL.StatementProperty(
                    byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                        field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(uri_path={}),
                        positional_constraint="STARTS_WITH",
                        search_string=path_prefix,
                        text_transformations=[wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="NONE")],
                    )),
            )),
        visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
            cloud_watch_metrics_enabled=True, metric_name=name, sampled_requests_enabled=True),
    )


def _managed(name: str, priority: int) -> wafv2.CfnWebACL.RuleProperty:
    return wafv2.CfnWebACL.RuleProperty(
        name=name,
        priority=priority,
        statement=wafv2.CfnWebACL.StatementProperty(
            managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                vendor_name="AWS", name=name)),
        override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
        visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
            cloud_watch_metrics_enabled=True, metric_name=name, sampled_requests_enabled=True),
    )


class RailPulseEdgeStack(Stack):
    """WAF web ACL for the dashboard's CloudFront distribution.

    CloudFront-scoped WAF resources can only be created in us-east-1, whatever region the
    application runs in — hence a separate stack, referenced cross-region by the web stack.
    """

    def __init__(self, scope: Construct, construct_id: str, *, stage: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.web_acl = wafv2.CfnWebACL(
            self, "WebAcl",
            name=f"railpulse-{stage}-dashboard",
            scope="CLOUDFRONT",
            default_action=wafv2.CfnWebACL.DefaultActionProperty(allow={}),
            visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True, metric_name=f"railpulse-{stage}-dashboard",
                sampled_requests_enabled=True),
            rules=[
                _managed("AWSManagedRulesAmazonIpReputationList", 0),
                _managed("AWSManagedRulesCommonRuleSet", 1),
                _managed("AWSManagedRulesKnownBadInputsRuleSet", 2),
                _rate_rule("ApiRateLimit", 3, "/api/", API_RATE_LIMIT_PER_5MIN),
                _rate_rule("AckRateLimit", 4, "/ack", ACK_RATE_LIMIT_PER_5MIN),
            ],
        )
        CfnOutput(self, "WebAclArn", value=self.web_acl.attr_arn)
