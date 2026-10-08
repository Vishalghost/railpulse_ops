"""Names shared across stacks as plain strings.

Passing a *name* (resolved at runtime) instead of a CloudFormation reference is how we break
stack-level dependency cycles, e.g. the ack Lambda (alerting stack) needs CloudFront's URL and
the origin secret (web stack), while the web stack needs the ack Lambda's Function URL.
"""


def origin_secret_name(stage: str) -> str:
    return f"railpulse/{stage}/origin-verify"


def ack_base_url_param(stage: str) -> str:
    return f"/railpulse/{stage}/web/ack-base-url"


def client_id_param(stage: str) -> str:
    return f"/railpulse/{stage}/web/user-pool-client-id"
