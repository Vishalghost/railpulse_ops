"""Step Functions task: page one rung of the escalation ladder.

Invoked with .waitForTaskToken for rungs that must acknowledge: this Lambda only *sends*;
the execution then waits (for free) until the ack page calls SendTaskSuccess or the rung
times out. Every contact gets their own single-use ack code mapped to the same task token,
so whoever confirms first is recorded by name.
"""

import logging
import os
import secrets
import time
from dataclasses import dataclass
from functools import lru_cache

import boto3

from ..channels import SmsSender, TopicPublisher, VoiceClips
from ..messages import UI_TEXT, Translator, compose, sms_body

log = logging.getLogger()
log.setLevel(logging.INFO)

ACK_GRACE_S = 86400  # ack links stay valid a day past the rung timeout (late acks still count)


@dataclass
class Deps:
    incidents: object  # DynamoDB Table
    translate: Translator
    sms: SmsSender
    topic: TopicPublisher
    voice: VoiceClips
    ack_base_url: str


def notify(event: dict, d: Deps) -> dict:
    incident, key, step, level = event["incident"], event["key"], event["step"], int(event["level"])
    token = event.get("taskToken")
    now = int(time.time())
    core_en = compose(incident)

    results = []
    for contact in step["contacts"]:
        lang = contact.get("lang", "en")
        core = d.translate(core_en, lang)
        ack_url = None
        if token:
            code = secrets.token_urlsafe(9)  # 12 URL-safe chars, 72 bits
            ack_url = f"{d.ack_base_url.rstrip('/')}/?c={code}"
            voice_key = d.voice.render(f"voice/{incident['incident_id'].replace(':', '/')}/{code}.mp3",
                                       core, lang, fallback_text=core_en)
            d.incidents.put_item(Item={
                "pk": f"ACK#{code}", "sk": "ACK",
                "task_token": token,
                "incident_pk": key["pk"], "incident_sk": key["sk"],
                "incident_id": incident["incident_id"],
                "first_seen": int(incident["first_seen"]),
                "level": level, "role": step["role"],
                "contact_id": contact["contact_id"], "contact_name": contact["name"],
                "lang": lang, "message": core, "message_en": core_en,
                "ui": {k: d.translate(v, lang) for k, v in UI_TEXT.items()},
                "voice_key": voice_key,
                "created_at": now,
                "expires_at": now + int(step["ack_timeout_s"]) + ACK_GRACE_S,
            })
        sms = d.sms.send(contact.get("phone"), sms_body(core, incident, ack_url))
        results.append({"contact_id": contact["contact_id"], "lang": lang, **sms})

    roster = ", ".join(f"{c['name']} ({c.get('lang', 'en')})" for c in step["contacts"])
    topic = d.topic.publish(
        subject=f"RailPulse P{int(incident['severity'])} {incident['type']} {incident['segment_id']} → {step['role']}",
        message=f"{sms_body(core_en, incident, None)}\n\nPaged ({step['role']}): {roster}\nIncident: {incident['incident_id']}",
        incident=incident,
    )

    d.incidents.put_item(Item={
        "pk": key["pk"], "sk": f"NOTIFY#{now:010d}#{level}#{step['role']}",
        "incident_id": incident["incident_id"], "level": level, "role": step["role"],
        "awaiting_ack": bool(token), "results": results, "topic": topic, "at": now,
    })
    d.incidents.update_item(
        Key=key,
        UpdateExpression="SET escalation_level = :l, escalation_role = :r, last_notified_at = :t",
        ExpressionAttributeValues={":l": level, ":r": step["role"], ":t": now},
    )

    delivered = topic["status"] == "sent" or any(r["status"] in ("sent", "dry_run") for r in results)
    if not delivered:
        # Fails the task → the state machine moves straight on to the next rung.
        raise RuntimeError(f"could not reach any {step['role']} for {incident['incident_id']}")
    log.info("paged %s rung %d for %s: %s", step["role"], level, incident["incident_id"], results)
    return {"level": level, "role": step["role"], "results": results}


def resolve_ack_base_url(ssm, env=os.environ) -> str:
    """The dashboard's CloudFront /ack/ URL, published to SSM by the web stack. ACK_BASE_URL
    (explicit override) wins; with neither available we can't build working links, so fail —
    the state machine then escalates to the next rung instead of sending dead links."""
    if env.get("ACK_BASE_URL"):
        return env["ACK_BASE_URL"]
    return ssm.get_parameter(Name=env["ACK_BASE_URL_PARAM"])["Parameter"]["Value"]


@lru_cache(maxsize=1)
def _deps() -> Deps:
    region = os.environ.get("AWS_REGION")
    return Deps(
        incidents=boto3.resource("dynamodb", region_name=region).Table(os.environ["INCIDENTS_TABLE"]),
        translate=Translator(boto3.client("translate", region_name=region)),
        sms=SmsSender(
            boto3.client("pinpoint-sms-voice-v2", region_name=region),
            mode=os.environ.get("SMS_MODE", "off"),
            origination_id=os.environ.get("SMS_ORIGINATION_ID") or None,
            in_entity_id=os.environ.get("IN_DLT_ENTITY_ID") or None,
            in_template_id=os.environ.get("IN_DLT_TEMPLATE_ID") or None,
        ),
        topic=TopicPublisher(boto3.client("sns", region_name=region), os.environ["ALERTS_TOPIC_ARN"]),
        voice=VoiceClips(boto3.client("polly", region_name=region), boto3.client("s3", region_name=region),
                         os.environ["VOICE_BUCKET"]),
        ack_base_url=resolve_ack_base_url(boto3.client("ssm", region_name=region)),
    )


def handler(event, _context):
    return notify(event, _deps())
