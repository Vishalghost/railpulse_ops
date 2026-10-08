from aws_cdk import Duration, RemovalPolicy
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_kms as kms
from aws_cdk import aws_s3 as s3
from constructs import Construct


class DataLayer(Construct):
    """DynamoDB tables and the S3 data lake.

    Every table uses generic `pk`/`sk` keys so the access patterns in PLAN.md section 3
    (SENSOR#…, SEG#…, DIV#…) can evolve without table migrations.
    """

    def __init__(self, scope: Construct, construct_id: str, *, ephemeral: bool, key: kms.IKey) -> None:
        super().__init__(scope, construct_id)
        removal = RemovalPolicy.DESTROY if ephemeral else RemovalPolicy.RETAIN

        def table(name: str, *, ttl: str | None = None, pitr: bool = False, **extra) -> ddb.TableV2:
            return ddb.TableV2(
                self,
                name,
                partition_key=ddb.Attribute(name="pk", type=ddb.AttributeType.STRING),
                sort_key=ddb.Attribute(name="sk", type=ddb.AttributeType.STRING),
                billing=ddb.Billing.on_demand(),
                # Customer-managed key: table grants (grant_read_data, …) include kms:Decrypt,
                # so access to the data and to the key are revoked together.
                encryption=ddb.TableEncryptionV2.customer_managed_key(key),
                time_to_live_attribute=ttl,
                point_in_time_recovery_specification=ddb.PointInTimeRecoverySpecification(
                    point_in_time_recovery_enabled=pitr
                ),
                removal_policy=removal,
                **extra,
            )

        # Hot data only — the full history lives in S3 via Firehose.
        self.telemetry = table("Telemetry", ttl="expires_at")

        # Also holds ACK#<code> items (single-use ack links) which expire via TTL.
        self.incidents = table(
            "Incidents",
            ttl="expires_at",
            pitr=True,
            global_secondary_indexes=[
                ddb.GlobalSecondaryIndexPropsV2(
                    index_name="gsi1",
                    partition_key=ddb.Attribute(name="gsi1pk", type=ddb.AttributeType.STRING),
                    sort_key=ddb.Attribute(name="gsi1sk", type=ddb.AttributeType.STRING),
                )
            ],
        )

        self.assets = table("Assets", pitr=True)
        self.baselines = table("Baselines")

        self.lake_bucket = s3.Bucket(
            self,
            "Lake",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.KMS,
            encryption_key=key,
            bucket_key_enabled=True,  # one data key per bucket-hour instead of per object: ~99% fewer KMS calls
            enforce_ssl=True,
            removal_policy=removal,
            auto_delete_objects=ephemeral,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="raw-to-glacier-ir",
                    prefix="raw/",
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.GLACIER_INSTANT_RETRIEVAL,
                            transition_after=Duration.days(90),
                        )
                    ],
                ),
                s3.LifecycleRule(id="expire-errors", prefix="errors/", expiration=Duration.days(30)),
                s3.LifecycleRule(id="expire-voice-clips", prefix="voice/", expiration=Duration.days(30)),
                s3.LifecycleRule(id="expire-athena-results", prefix="athena-results/", expiration=Duration.days(7)),
            ],
        )
