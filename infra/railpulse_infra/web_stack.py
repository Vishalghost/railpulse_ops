"""Phase 4: dashboard + API on an Elastic Beanstalk web environment.

Viewer ─HTTPS─▶ CloudFront ─HTTP─▶ ALB ─▶ EC2 (Docker Compose: nginx + FastAPI)
                 (default *.cloudfront.net cert gives HTTPS without a custom domain,
                  which Cognito requires for non-localhost callback URLs)
"""

import shutil
import sys
from pathlib import Path

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_elasticbeanstalk as eb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_location as location
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_ssm as ssm
from aws_cdk import custom_resources as cr
from constructs import Construct

from .alerting_stack import RailPulseAlertingStack
from .core_stack import RailPulseCoreStack
from .eb_common import (
    PY_IGNORE,
    ROOT,
    SHARED_EBEXTENSIONS,
    allow_self_imds_update,
    health_metrics_config,
    imds_options,
    source_bundle,
)
from .names import ack_base_url_param, client_id_param, origin_secret_name

ROLE_GROUPS = {"Admin": "All divisions, all actions", "DEN": "Divisional Engineer",
               "SSE": "Section Engineer (P.Way)", "Gangman": "Track maintainer"}
DIVISION_GROUPS = ("DLI", "BB", "BCT", "MAS", "HWH", "SC", "SBC", "LKO")
LOCAL_DEV_ORIGIN = "http://localhost:3000"


def _add_dashboard(out: Path) -> None:
    """Copy the Next.js static export into the nginx image's build context."""
    static = out / "webproxy" / "static"
    built = ROOT / "dashboard" / "out"
    if built.is_dir():
        shutil.copytree(built, static, dirs_exist_ok=True)
        return
    print("[railpulse] dashboard/out missing — run `npm run build` in dashboard/ before deploying", file=sys.stderr)
    static.mkdir(parents=True, exist_ok=True)
    (static / "index.html").write_text(
        "<!doctype html><title>RailPulse</title><p>Dashboard not built. "
        "Run <code>npm run build</code> in dashboard/ and redeploy.</p>")


def _opt(namespace: str, name: str, value) -> eb.CfnEnvironment.OptionSettingProperty:
    return eb.CfnEnvironment.OptionSettingProperty(namespace=namespace, option_name=name, value=str(value))


class RailPulseWebStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, stage: str, core: RailPulseCoreStack,
                 alerting: RailPulseAlertingStack, application_name: str, solution_stack: str,
                 web_acl_arn: str | None = None, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        data = core.data
        vpc = core.network.vpc
        ephemeral = stage != "prod"
        removal = RemovalPolicy.DESTROY if ephemeral else RemovalPolicy.RETAIN

        # --- Origin lock (two layers) -----------------------------------------------------------
        # 1. ALB security group admits only CloudFront's origin-facing IP ranges (AWS-managed
        #    prefix list, looked up at deploy time because its ID differs per region).
        # 2. CloudFront adds a secret header that nginx checks — the prefix list alone would still
        #    admit *anyone's* CloudFront distribution pointed at our ALB.
        prefix_list = cr.AwsCustomResource(
            self, "CloudFrontPrefixList",
            on_update=cr.AwsSdkCall(
                service="EC2", action="describeManagedPrefixLists",
                parameters={"Filters": [{"Name": "prefix-list-name",
                                         "Values": ["com.amazonaws.global.cloudfront.origin-facing"]}]},
                physical_resource_id=cr.PhysicalResourceId.of("cloudfront-origin-facing"),
                output_paths=["PrefixLists.0.PrefixListId"],
            ),
            policy=cr.AwsCustomResourcePolicy.from_sdk_calls(resources=cr.AwsCustomResourcePolicy.ANY_RESOURCE),
            install_latest_aws_sdk=False,
        )
        alb_sg = ec2.SecurityGroup(self, "AlbSecurityGroup", vpc=vpc, allow_all_outbound=True,
                                   description="RailPulse ALB: HTTP from CloudFront origin-facing ranges only")
        alb_sg.add_ingress_rule(ec2.Peer.prefix_list(prefix_list.get_response_field("PrefixLists.0.PrefixListId")),
                                ec2.Port.tcp(80), "CloudFront origin-facing")
        origin_secret = secretsmanager.Secret(
            self, "OriginVerifySecret",
            # Fixed name: the ack Lambda (alerting stack) reads it by name to check /ack/* requests.
            secret_name=origin_secret_name(stage),
            description="Shared secret: CloudFront → nginx and ack Lambda X-Origin-Verify header",
            generate_secret_string=secretsmanager.SecretStringGenerator(exclude_punctuation=True, password_length=40),
            removal_policy=removal,
        )
        origin_secret_value = origin_secret.secret_value.unsafe_unwrap()  # CFN dynamic reference, not plaintext in the template

        # Names fixed up front. Several resources must reference each other in a loop
        # (EB env → CloudFront → Cognito client / map key → EB env); passing *names* as
        # plain strings, resolved at runtime, breaks the CloudFormation dependency cycle.
        client_id_param_name = client_id_param(stage)
        map_key_name = f"railpulse-{stage}-map"

        # --- Cognito ----------------------------------------------------------------------
        pool = cognito.UserPool(
            self, "Users",
            user_pool_name=f"railpulse-{stage}",
            self_sign_up_enabled=False,  # admins create accounts (tools/create_user.py)
            sign_in_aliases=cognito.SignInAliases(username=True, email=True),
            password_policy=cognito.PasswordPolicy(min_length=12, require_symbols=False),
            mfa=cognito.Mfa.OPTIONAL,
            mfa_second_factor=cognito.MfaSecondFactor(otp=True, sms=False),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            removal_policy=removal,
        )
        for name, desc in ROLE_GROUPS.items():
            cognito.CfnUserPoolGroup(self, f"Group{name}", user_pool_id=pool.user_pool_id,
                                     group_name=name, description=desc)
        for div in DIVISION_GROUPS:
            cognito.CfnUserPoolGroup(self, f"GroupDiv{div}", user_pool_id=pool.user_pool_id,
                                     group_name=f"DIV_{div}", description=f"Can see {div} division")
        domain = pool.add_domain("Domain", cognito_domain=cognito.CognitoDomainOptions(
            domain_prefix=f"railpulse-{stage}-{self.account}"))

        # --- Source bundle --------------------------------------------------------------------
        # docker-compose.yml + api + nginx (+ dashboard export) + shared .ebextensions
        bundle = source_bundle(self, "Bundle", path=ROOT / "webproxy", copies=[
            (ROOT / "docker-compose.yml", "docker-compose.yml", ()),
            (ROOT / "api", "api", PY_IGNORE),
            (ROOT / "webproxy", "webproxy", ("static",)),
            (SHARED_EBEXTENSIONS, ".ebextensions", ()),
        ], extra=_add_dashboard)
        version = eb.CfnApplicationVersion(
            self, "Version",
            application_name=application_name,
            source_bundle=eb.CfnApplicationVersion.SourceBundleProperty(
                s3_bucket=bundle.s3_bucket_name, s3_key=bundle.s3_object_key),
        )

        # --- IAM ------------------------------------------------------------------------------
        role = iam.Role(
            self, "InstanceRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("AWSElasticBeanstalkWebTier")],
        )
        data.incidents.grant_read_write_data(role)  # acknowledge / resolve
        data.assets.grant_read_data(role)
        data.telemetry.grant_read_data(role)
        role.add_to_policy(iam.PolicyStatement(
            actions=["ssm:GetParameter"],
            resources=[f"arn:{self.partition}:ssm:{self.region}:{self.account}:parameter{client_id_param_name}"]))
        role.add_to_policy(iam.PolicyStatement(
            actions=["geo:DescribeKey"],
            resources=[f"arn:{self.partition}:geo:{self.region}:{self.account}:api-key/{map_key_name}"]))
        allow_self_imds_update(role, f"railpulse-{stage}-web")
        profile = iam.CfnInstanceProfile(self, "InstanceProfile", roles=[role.role_name])

        service_role = iam.Role(
            self, "ServiceRole",
            assumed_by=iam.ServicePrincipal("elasticbeanstalk.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSElasticBeanstalkEnhancedHealth"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AWSElasticBeanstalkManagedUpdatesCustomerRolePolicy"),
            ],
        )

        # --- EB web environment ---------------------------------------------------------------
        env_ns = "aws:elasticbeanstalk:application:environment"
        options = [
            _opt("aws:elasticbeanstalk:environment", "EnvironmentType", "LoadBalanced"),
            _opt("aws:elasticbeanstalk:environment", "LoadBalancerType", "application"),
            _opt("aws:elasticbeanstalk:environment", "ServiceRole", service_role.role_arn),
            _opt("aws:autoscaling:launchconfiguration", "IamInstanceProfile", profile.ref),
            *imds_options(),
            _opt("aws:ec2:instances", "InstanceTypes", "t3.small"),
            # ALB in public subnets. Instances: private (standard) or public with inbound only
            # from the ALB's security group (lean). See network.py.
            _opt("aws:ec2:vpc", "VPCId", vpc.vpc_id),
            _opt("aws:ec2:vpc", "Subnets", core.network.instance_subnet_ids),
            _opt("aws:ec2:vpc", "ELBSubnets", core.network.public_subnet_ids),
            _opt("aws:ec2:vpc", "ELBScheme", "public"),
            _opt("aws:ec2:vpc", "AssociatePublicIpAddress", core.network.instances_get_public_ip),
            # Our SG replaces EB's default 0.0.0.0/0 one; EB opens instances to it automatically.
            _opt("aws:elbv2:loadbalancer", "SecurityGroups", alb_sg.security_group_id),
            _opt("aws:elbv2:loadbalancer", "ManagedSecurityGroup", alb_sg.security_group_id),
            # dev keeps one instance to save cost; prod runs two across AZs.
            _opt("aws:autoscaling:asg", "MinSize", 1 if ephemeral else 2),
            _opt("aws:autoscaling:asg", "MaxSize", 8),
            # The pitch: ALB-driven autoscaling at CPU > 70%.
            _opt("aws:autoscaling:trigger", "MeasureName", "CPUUtilization"),
            _opt("aws:autoscaling:trigger", "Statistic", "Average"),
            _opt("aws:autoscaling:trigger", "Unit", "Percent"),
            _opt("aws:autoscaling:trigger", "Period", 1),
            _opt("aws:autoscaling:trigger", "BreachDuration", 2),
            _opt("aws:autoscaling:trigger", "UpperThreshold", 70),
            _opt("aws:autoscaling:trigger", "LowerThreshold", 30),
            _opt("aws:elasticbeanstalk:environment:process:default", "HealthCheckPath", "/api/health"),
            _opt("aws:elasticbeanstalk:environment:process:default", "MatcherHTTPCode", 200),
            _opt("aws:elasticbeanstalk:environment:process:default", "DeregistrationDelay", 20),
            _opt("aws:elbv2:loadbalancer", "IdleTimeout", 120),  # > SSE ping interval
            # Zero-downtime deploys: new batch comes up healthy before old instances leave.
            _opt("aws:elasticbeanstalk:command", "DeploymentPolicy", "RollingWithAdditionalBatch"),
            _opt("aws:elasticbeanstalk:command", "BatchSizeType", "Fixed"),
            _opt("aws:elasticbeanstalk:command", "BatchSize", 1),
            _opt("aws:elasticbeanstalk:healthreporting:system", "SystemType", "enhanced"),
            health_metrics_config("ApplicationRequests5xx", "ApplicationLatencyP90"),
            _opt("aws:elasticbeanstalk:cloudwatch:logs", "StreamLogs", "true"),
            _opt("aws:elasticbeanstalk:cloudwatch:logs", "RetentionInDays", 14),
            _opt(env_ns, "RAILPULSE_REGION", self.region),
            _opt(env_ns, "INCIDENTS_TABLE", data.incidents.table_name),
            _opt(env_ns, "ASSETS_TABLE", data.assets.table_name),
            _opt(env_ns, "TELEMETRY_TABLE", data.telemetry.table_name),
            _opt(env_ns, "USER_POOL_ID", pool.user_pool_id),
            _opt(env_ns, "COGNITO_DOMAIN", domain.base_url()),
            _opt(env_ns, "USER_POOL_CLIENT_ID_PARAM", client_id_param_name),
            _opt(env_ns, "MAP_API_KEY_NAME", map_key_name),
            _opt(env_ns, "CORS_ORIGINS", LOCAL_DEV_ORIGIN),
            _opt(env_ns, "ORIGIN_VERIFY_SECRET", origin_secret_value),
        ]
        env = eb.CfnEnvironment(
            self, "WebEnv",
            application_name=application_name,
            environment_name=f"railpulse-{stage}-web",
            solution_stack_name=solution_stack,
            tier=eb.CfnEnvironment.TierProperty(name="WebServer", type="Standard"),
            version_label=version.ref,
            option_settings=options,
        )

        # --- CloudFront -----------------------------------------------------------------------
        env.node.add_dependency(vpc)
        origin = origins.HttpOrigin(
            env.attr_endpoint_url,
            protocol_policy=cloudfront.OriginProtocolPolicy.HTTP_ONLY,
            read_timeout=Duration.seconds(60),
            custom_headers={"X-Origin-Verify": origin_secret_value},
        )
        # API: never served from cache, but Authorization must reach the origin. CloudFront only
        # forwards Authorization on GET when it's part of the cache key, so key on it with TTL 0.
        api_cache = cloudfront.CachePolicy(
            self, "ApiCachePolicy",
            default_ttl=Duration.seconds(0), min_ttl=Duration.seconds(0), max_ttl=Duration.seconds(1),
            header_behavior=cloudfront.CacheHeaderBehavior.allow_list("Authorization"),
            query_string_behavior=cloudfront.CacheQueryStringBehavior.all(),
            cookie_behavior=cloudfront.CacheCookieBehavior.none(),
        )
        dist = cloudfront.Distribution(
            self, "Cdn",
            comment=f"RailPulse {stage} dashboard",
            web_acl_id=web_acl_arn,  # WAF lives in us-east-1 (edge_stack.py)
            price_class=cloudfront.PriceClass.PRICE_CLASS_200,  # includes India edge locations
            http_version=cloudfront.HttpVersion.HTTP2_AND_3,
            default_behavior=cloudfront.BehaviorOptions(
                origin=origin,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                compress=True,
            ),
            additional_behaviors={
                "/api/*": cloudfront.BehaviorOptions(
                    origin=origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    cache_policy=api_cache,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                ),
                # SMS ack links: same WAF and HTTPS edge as the dashboard. The Lambda refuses
                # requests without the origin secret, so its raw Function URL is useless alone.
                # (Not OAC: OAC-signed POSTs to Lambda need a body hash a plain HTML form can't send.)
                "/ack*": cloudfront.BehaviorOptions(
                    origin=origins.FunctionUrlOrigin(
                        alerting.ack_url,
                        custom_headers={"X-Origin-Verify": origin_secret_value},
                        read_timeout=Duration.seconds(30),
                    ),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                ),
            },
        )
        site = f"https://{dist.distribution_domain_name}"
        # The notify Lambda reads this at cold start to build SMS ack links.
        ssm.StringParameter(self, "AckBaseUrlParam", parameter_name=ack_base_url_param(stage),
                            string_value=f"{site}/ack/")

        # --- Cognito app client (needs the CloudFront URL) ------------------------------------
        client = pool.add_client(
            "Dashboard",
            generate_secret=False,  # browser app: PKCE instead of a secret
            auth_flows=cognito.AuthFlow(user_srp=True),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL, cognito.OAuthScope.PROFILE],
                callback_urls=[f"{site}/callback", f"{LOCAL_DEV_ORIGIN}/callback"],
                logout_urls=[f"{site}/", f"{LOCAL_DEV_ORIGIN}/"],
            ),
            supported_identity_providers=[cognito.UserPoolClientIdentityProvider.COGNITO],
            access_token_validity=Duration.hours(1),
            id_token_validity=Duration.hours(1),
            refresh_token_validity=Duration.hours(12),  # one shift
            prevent_user_existence_errors=True,
        )
        ssm.StringParameter(self, "ClientIdParam", parameter_name=client_id_param_name,
                            string_value=client.user_pool_client_id)

        # --- Amazon Location (Maps v2) API key, usable only from the dashboard's origin ---------
        location.CfnAPIKey(
            self, "MapKey",
            key_name=map_key_name,
            no_expiry=True,
            force_delete=True,
            force_update=True,
            restrictions=location.CfnAPIKey.ApiKeyRestrictionsProperty(
                allow_actions=["geo-maps:GetTile", "geo-maps:GetStaticMap"],
                allow_resources=[f"arn:{self.partition}:geo-maps:{self.region}::provider/default"],
                allow_referers=[f"{site}/*", f"{LOCAL_DEV_ORIGIN}/*"],
            ),
        )

        core.alarm(
            "WebEnvDegraded",
            "Dashboard/API environment health is Degraded or worse",
            cloudwatch.Metric(namespace="AWS/ElasticBeanstalk", metric_name="EnvironmentHealth",
                              dimensions_map={"EnvironmentName": f"railpulse-{stage}-web"},
                              statistic="Maximum", period=Duration.minutes(1)),
            threshold=20, evaluation_periods=5, scope=self,
            comparison=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        )

        CfnOutput(self, "DashboardUrl", value=site)
        CfnOutput(self, "UserPoolId", value=pool.user_pool_id)
        CfnOutput(self, "UserPoolClientId", value=client.user_pool_client_id)
        CfnOutput(self, "CognitoDomain", value=domain.base_url())
        CfnOutput(self, "WebEnvironmentUrl", value=f"http://{env.attr_endpoint_url}")
