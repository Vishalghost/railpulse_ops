import boto3

DEFAULT_STACK = "RailPulse-dev-Core"
DEFAULT_REGION = "ap-south-1"


def stack_outputs(stack_name: str, region: str) -> dict[str, str]:
    cfn = boto3.client("cloudformation", region_name=region)
    stack = cfn.describe_stacks(StackName=stack_name)["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}
