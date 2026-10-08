"""Create (or tear down) the simulator's IoT certificate.

    python provision.py              # create cert, attach SimulatorPolicy, save endpoint
    python provision.py --teardown   # detach + delete cert (run before `cdk destroy`,
                                     # otherwise the attached policy blocks stack deletion)
"""

import argparse
import urllib.request
from pathlib import Path

import boto3

from railpulse_sim.aws import DEFAULT_REGION, DEFAULT_STACK, stack_outputs

THING_NAME = "railpulse-simulator"
ROOT_CA_URL = "https://www.amazontrust.com/repository/AmazonRootCA1.pem"


def provision(iot, policy: str, certs: Path, force: bool) -> None:
    certs.mkdir(exist_ok=True)
    arn_file = certs / "certificate_arn.txt"
    if arn_file.exists() and not force:
        print(f"certificate already provisioned ({arn_file.read_text().strip()}); use --force to replace")
    else:
        resp = iot.create_keys_and_certificate(setAsActive=True)
        (certs / "cert.pem").write_text(resp["certificatePem"])
        (certs / "private.key").write_text(resp["keyPair"]["PrivateKey"])
        arn_file.write_text(resp["certificateArn"])
        iot.attach_policy(policyName=policy, target=resp["certificateArn"])
        try:
            iot.create_thing(thingName=THING_NAME)
        except iot.exceptions.ResourceAlreadyExistsException:
            pass
        iot.attach_thing_principal(thingName=THING_NAME, principal=resp["certificateArn"])
        print(f"created certificate {resp['certificateArn']}")

    ca = certs / "AmazonRootCA1.pem"
    if not ca.exists():
        with urllib.request.urlopen(ROOT_CA_URL, timeout=15) as r:
            ca.write_bytes(r.read())

    endpoint = iot.describe_endpoint(endpointType="iot:Data-ATS")["endpointAddress"]
    (certs / "endpoint.txt").write_text(endpoint)
    print(f"endpoint {endpoint} saved to {certs / 'endpoint.txt'}")


def teardown(iot, policy: str, certs: Path) -> None:
    arn_file = certs / "certificate_arn.txt"
    if not arn_file.exists():
        print("nothing to tear down")
        return
    arn = arn_file.read_text().strip()
    cert_id = arn.rsplit("/", 1)[-1]
    iot.detach_policy(policyName=policy, target=arn)
    try:
        iot.detach_thing_principal(thingName=THING_NAME, principal=arn)
        iot.delete_thing(thingName=THING_NAME)
    except iot.exceptions.ResourceNotFoundException:
        pass
    iot.update_certificate(certificateId=cert_id, newStatus="INACTIVE")
    iot.delete_certificate(certificateId=cert_id)
    for name in ("cert.pem", "private.key", "certificate_arn.txt"):
        (certs / name).unlink(missing_ok=True)
    print(f"deleted certificate {cert_id}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--stack", default=DEFAULT_STACK)
    p.add_argument("--region", default=DEFAULT_REGION)
    p.add_argument("--certs", type=Path, default=Path(__file__).resolve().parent / "certs")
    p.add_argument("--force", action="store_true", help="replace an existing certificate")
    p.add_argument("--teardown", action="store_true")
    args = p.parse_args()

    policy = stack_outputs(args.stack, args.region)["SimulatorPolicyName"]
    iot = boto3.client("iot", region_name=args.region)
    if args.teardown:
        teardown(iot, policy, args.certs)
    else:
        provision(iot, policy, args.certs, args.force)


if __name__ == "__main__":
    main()
