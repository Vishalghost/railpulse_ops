"""EventBridge (AnomalyDetected / IncidentEscalated) → start one escalation execution.

Events are at-least-once. The execution name is derived from (incident, severity), and
Step Functions refuses a second execution with the same name — so duplicates are no-ops.
"""

import logging
import os
import re
from functools import lru_cache

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from ..channels import dumps
from ..policy import build_ladder

log = logging.getLogger()
log.setLevel(logging.INFO)


def execution_name(incident: dict) -> str:
    raw = f"{incident['incident_id']}-P{int(incident['severity'])}"
    return re.sub(r"[^A-Za-z0-9_-]", "-", raw)[:80]


def incident_key(incident: dict) -> dict:
    # Mirrors worker/app/store.py IncidentRepo layout.
    return {"pk": f"SEG#{incident['segment_id']}",
            "sk": f"INC#{int(incident['first_seen']):013d}#{incident['type']}"}


def load_contacts(assets_table, division: str) -> list[dict]:
    resp = assets_table.query(
        KeyConditionExpression=Key("pk").eq(f"DIV#{division}") & Key("sk").begins_with("CONTACT#")
    )
    return resp["Items"]


def start(event: dict, assets_table, sfn, state_machine_arn: str, timeout_override_s: int | None) -> dict:
    incident = event["detail"]
    severity = int(incident["severity"])
    km = incident.get("km_from")
    ladder = build_ladder(load_contacts(assets_table, incident["division"]), severity,
                          float(km) if km is not None else None, timeout_override_s)
    if not ladder:
        log.warning("no escalation ladder for %s (P%s, %s)", incident["incident_id"], severity, incident["division"])
        return {"started": False, "reason": "no_ladder"}

    name = execution_name(incident)
    try:
        sfn.start_execution(
            stateMachineArn=state_machine_arn,
            name=name,
            input=dumps({"incident": incident, "key": incident_key(incident), "ladder": ladder}),
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "ExecutionAlreadyExists":
            raise
        log.info("duplicate event for %s — execution already exists", name)
        return {"started": False, "reason": "duplicate", "execution": name}
    log.info("started escalation %s with %d rung(s)", name, len(ladder))
    return {"started": True, "execution": name}


@lru_cache(maxsize=1)
def _deps():
    region = os.environ.get("AWS_REGION")
    return (
        boto3.resource("dynamodb", region_name=region).Table(os.environ["ASSETS_TABLE"]),
        boto3.client("stepfunctions", region_name=region),
    )


def handler(event, _context):
    table, sfn = _deps()
    override = int(os.environ.get("ACK_TIMEOUT_OVERRIDE_S") or 0) or None
    return start(event, table, sfn, os.environ["STATE_MACHINE_ARN"], override)
