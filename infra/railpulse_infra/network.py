from aws_cdk import aws_ec2 as ec2
from constructs import Construct


class NetworkLayer(Construct):
    """VPC for both Elastic Beanstalk environments. Two layouts:

    standard (prod)
      public   — ALB and NAT gateway(s) only
      private  — EC2 instances, no public IPs; outbound via NAT (Docker Hub/PyPI at build time,
                 Cognito JWKS, EventBridge, EB service endpoints); SQS via an interface endpoint
    lean (dev default — fits a ~$50 budget)
      public   — ALB *and* instances, with public IPs but no inbound path except from the ALB
                 (EB's instance security group admits only the ALB's group). No NAT gateway and
                 no interface endpoint: the two largest always-on network charges.

    Both layouts get the free DynamoDB and S3 gateway endpoints, which carry the bulk of the
    traffic (every telemetry write) off the NAT/internet path.
    """

    def __init__(self, scope: Construct, construct_id: str, *, lean: bool, nat_gateways: int = 1) -> None:
        super().__init__(scope, construct_id)
        self.lean = lean
        subnets = [ec2.SubnetConfiguration(name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24)]
        if not lean:
            subnets.append(ec2.SubnetConfiguration(name="private", subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                                                   cidr_mask=22))
        self.vpc = ec2.Vpc(
            self, "Vpc",
            ip_addresses=ec2.IpAddresses.cidr("10.40.0.0/16"),
            max_azs=2,
            nat_gateways=0 if lean else nat_gateways,
            subnet_configuration=subnets,
            gateway_endpoints={
                "DynamoDb": ec2.GatewayVpcEndpointOptions(service=ec2.GatewayVpcEndpointAwsService.DYNAMODB),
                "S3": ec2.GatewayVpcEndpointOptions(service=ec2.GatewayVpcEndpointAwsService.S3),
            },
        )
        if not lean:
            self.vpc.add_interface_endpoint(
                "Sqs",
                service=ec2.InterfaceVpcEndpointAwsService.SQS,
                subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
                private_dns_enabled=True,
            )

    @property
    def instance_subnet_ids(self) -> str:
        subnets = self.vpc.public_subnets if self.lean else self.vpc.private_subnets
        return ",".join(s.subnet_id for s in subnets)

    @property
    def public_subnet_ids(self) -> str:
        return ",".join(s.subnet_id for s in self.vpc.public_subnets)

    @property
    def instances_get_public_ip(self) -> str:
        return "true" if self.lean else "false"
