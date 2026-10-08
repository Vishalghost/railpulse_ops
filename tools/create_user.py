"""Create a dashboard user in the RailPulse Cognito pool and put them in role + division groups.

    python tools/create_user.py --username sse.delhi --email sse@example.com --role SSE --division DLI
    python tools/create_user.py --username admin --email you@example.com --role Admin

Prints a one-time temporary password; Cognito asks for a new one at first sign-in.
"""

import argparse
import secrets
import string

import boto3

ROLES = ("Admin", "DEN", "SSE", "Gangman")
DIVISIONS = ("DLI", "MAS", "BB")


def temp_password() -> str:
    alphabet = string.ascii_letters + string.digits
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(16))
        if any(c.islower() for c in pw) and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw):
            return pw


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--username", required=True)
    p.add_argument("--email", required=True)
    p.add_argument("--role", required=True, choices=ROLES)
    p.add_argument("--division", action="append", default=[], choices=DIVISIONS,
                   help="repeatable; not needed for Admin")
    p.add_argument("--stack", default="RailPulse-dev-Web")
    p.add_argument("--region", default="ap-south-1")
    args = p.parse_args()
    if args.role != "Admin" and not args.division:
        p.error("non-admin users need at least one --division")

    cfn = boto3.client("cloudformation", region_name=args.region)
    outputs = {o["OutputKey"]: o["OutputValue"]
               for o in cfn.describe_stacks(StackName=args.stack)["Stacks"][0].get("Outputs", [])}
    pool_id = outputs["UserPoolId"]

    idp = boto3.client("cognito-idp", region_name=args.region)
    password = temp_password()
    idp.admin_create_user(
        UserPoolId=pool_id,
        Username=args.username,
        TemporaryPassword=password,
        MessageAction="SUPPRESS",  # we hand the password over ourselves
        UserAttributes=[{"Name": "email", "Value": args.email}, {"Name": "email_verified", "Value": "true"}],
    )
    for group in [args.role] + [f"DIV_{d}" for d in args.division]:
        idp.admin_add_user_to_group(UserPoolId=pool_id, Username=args.username, GroupName=group)

    print(f"created {args.username} ({args.role}{', ' + ', '.join(args.division) if args.division else ''})")
    print(f"temporary password: {password}")
    print(f"sign in at: {outputs.get('DashboardUrl', '(see DashboardUrl output)')}")


if __name__ == "__main__":
    main()
