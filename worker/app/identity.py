import json
import logging
import os

import boto3

log = logging.getLogger("railpulse.identity")


def log_aws_identity() -> bool:
    """Startup self-check: can this container get AWS credentials? On EB the answer depends on
    the IMDS hop limit (ebextensions-shared/02-imds-hop-limit.config) — say so loudly if not,
    rather than surfacing later as every message failing into the DLQ."""
    try:
        ident = boto3.client("sts", region_name=os.environ.get("RAILPULSE_REGION")).get_caller_identity()
    except Exception as e:  # noqa: BLE001 — any failure here means the same thing
        log.error(json.dumps({"event": "aws_identity_failed", "error": str(e)[:300],
                              "hint": "container cannot reach instance-role credentials; check IMDS hop limit"}))
        return False
    log.info(json.dumps({"event": "aws_identity", "arn": ident["Arn"]}))
    return True
