import json
import shutil
from collections.abc import Callable
from pathlib import Path

import jsii
from aws_cdk import AssetHashType, BundlingOptions, DockerImage, ILocalBundling
from aws_cdk import aws_elasticbeanstalk as eb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3_assets as s3_assets
from constructs import Construct

ROOT = Path(__file__).resolve().parents[2]
SHARED_EBEXTENSIONS = ROOT / "ebextensions-shared"
PY_IGNORE = ("tests", "__pycache__", ".pytest_cache", "pytest.ini", "requirements-dev.txt")


def opt(namespace: str, name: str, value) -> eb.CfnEnvironment.OptionSettingProperty:
    return eb.CfnEnvironment.OptionSettingProperty(namespace=namespace, option_name=name, value=str(value))


def health_metrics_config(*extra: str) -> eb.CfnEnvironment.OptionSettingProperty:
    """Enhanced health only publishes EnvironmentHealth to CloudWatch by default; these extra
    environment-level metrics (≈ $0.30/metric/month each) feed the ops dashboard's scaling graphs."""
    metrics = {name: 60 for name in ("InstancesOk", "InstancesDegraded", "InstancesSevere", *extra)}
    doc = {"Version": 1, "CloudWatchMetrics": {"Environment": metrics, "Instance": {}}, "Rules": {"Environment": {}}}
    return opt("aws:elasticbeanstalk:healthreporting:system", "ConfigDocument", json.dumps(doc, separators=(",", ":")))


def imds_options() -> list[eb.CfnEnvironment.OptionSettingProperty]:
    # IMDSv2 only. Containers still reach it because 02-imds-hop-limit.config sets hop limit 2.
    return [opt("aws:autoscaling:launchconfiguration", "DisableIMDSv1", "true")]


def allow_self_imds_update(role: iam.Role, environment_name: str) -> None:
    """Let instances raise their own IMDS hop limit (02-imds-hop-limit.config) — and only their
    own environment's instances, which EB tags with elasticbeanstalk:environment-name."""
    role.add_to_policy(iam.PolicyStatement(
        actions=["ec2:ModifyInstanceMetadataOptions"],
        resources=["*"],
        conditions={"StringEquals": {"ec2:ResourceTag/elasticbeanstalk:environment-name": environment_name}},
    ))


Copy = tuple[Path, str, tuple[str, ...]]  # (source, destination inside bundle, ignore patterns)


@jsii.implements(ILocalBundling)
class _LocalBundle:
    """Assemble an EB source bundle with plain file copies — no Docker needed at synth time."""

    def __init__(self, copies: list[Copy], extra: Callable[[Path], None] | None) -> None:
        self._copies = copies
        self._extra = extra

    def try_bundle(self, output_dir: str, options=None, **_kwargs) -> bool:
        out = Path(output_dir)
        for src, dest, ignore in self._copies:
            target = out / dest
            if src.is_dir():
                shutil.copytree(src, target, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns(*ignore) if ignore else None)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
        if self._extra:
            self._extra(out)
        return True


def source_bundle(scope: Construct, construct_id: str, *, path: Path, copies: list[Copy],
                  extra: Callable[[Path], None] | None = None) -> s3_assets.Asset:
    """`path` only anchors the asset; the hash is of the assembled output, so editing any copied
    file (including the shared ebextensions) produces a new application version."""
    return s3_assets.Asset(
        scope, construct_id,
        path=str(path),
        asset_hash_type=AssetHashType.OUTPUT,
        bundling=BundlingOptions(
            image=DockerImage.from_registry("public.ecr.aws/docker/library/alpine:3"),  # unused: local bundling
            local=_LocalBundle(copies, extra),
        ),
    )
