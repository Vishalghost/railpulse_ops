"""Ack page: Lambda Function URL, served to users through CloudFront + WAF at <dashboard>/ack/.

GET  /?c=<code>  shows the alert (in the contact's language, with a Polly voice clip) and a
                 confirm button. GET never changes state: SMS apps and link scanners fetch
                 links to build previews, and that must not acknowledge a P1 crack.
POST /?c=<code>  confirms: SendTaskSuccess to the waiting escalation, incident → ACKNOWLEDGED.
"""

import hmac
import html
import json
import logging
import os
import re
import time
from functools import lru_cache

import boto3
from botocore.exceptions import ClientError

from ..channels import VoiceClips

log = logging.getLogger()
log.setLevel(logging.INFO)

CODE_RE = re.compile(r"^[A-Za-z0-9_-]{12}$")
LATE_ERRORS = {"TaskTimedOut", "InvalidToken", "TaskDoesNotExist"}


def _page(status: int, lang: str, body: str) -> dict:
    doc = f"""<!doctype html><html lang="{html.escape(lang)}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>RailPulse alert</title>
<style>body{{font-family:system-ui,sans-serif;margin:0;padding:1.25rem;max-width:32rem;background:#111;color:#f4f4f4}}
.sev{{display:inline-block;padding:.2rem .6rem;border-radius:.3rem;background:#c62828;font-weight:700}}
p{{font-size:1.15rem;line-height:1.5}} .en{{color:#aaa;font-size:.95rem}}
button{{width:100%;padding:1.1rem;font-size:1.15rem;border:0;border-radius:.5rem;background:#2e7d32;color:#fff}}
audio{{width:100%;margin:1rem 0}}</style></head><body>{body}</body></html>"""
    return {"statusCode": status, "headers": {"Content-Type": "text/html; charset=utf-8",
                                              "Cache-Control": "no-store"}, "body": doc}


def _not_found() -> dict:
    return _page(404, "en", "<p>This alert link is invalid or has expired.</p>")


def origin_ok(event: dict, expected_secret: str | None) -> bool:
    """True if the request came through CloudFront (which adds X-Origin-Verify).
    `expected_secret=None` means the check is off (tests, local runs)."""
    if expected_secret is None:
        return True
    got = (event.get("headers") or {}).get("x-origin-verify", "")  # Function URLs lower-case header names
    return hmac.compare_digest(got.encode(), expected_secret.encode())


def respond(event: dict, incidents, sfn, voice: VoiceClips | None, expected_secret: str | None = None) -> dict:
    if not origin_ok(event, expected_secret):
        return _page(403, "en", "<p>Open this alert from the link in your SMS.</p>")
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET").upper()
    code = (event.get("queryStringParameters") or {}).get("c", "")
    if not CODE_RE.match(code):
        return _not_found()
    ack_key = {"pk": f"ACK#{code}", "sk": "ACK"}
    item = incidents.get_item(Key=ack_key, ConsistentRead=True).get("Item")
    now = int(time.time())
    if not item or int(item["expires_at"]) < now:
        return _not_found()
    lang, ui = item.get("lang", "en"), item.get("ui", {})

    if method == "GET":
        if item.get("used_at"):
            return _page(200, lang, f"<p>{html.escape(ui.get('already', 'Already confirmed.'))}</p>")
        audio = ""
        if item.get("voice_key") and voice:
            audio = f'<audio controls autoplay src="{html.escape(voice.presign(item["voice_key"]))}"></audio>'
        en = "" if lang == "en" else f'<p class="en">{html.escape(item["message_en"])}</p>'
        return _page(200, lang, f"""<span class="sev">RailPulse · {html.escape(item['role'])}</span>
<p>{html.escape(item['message'])}</p>{en}
<p class="en">{html.escape(item['incident_id'])}</p>{audio}
<form method="post" action="?c={html.escape(code)}"><button type="submit">{html.escape(ui.get('button', 'Confirm'))}</button></form>""")

    if method != "POST":
        return {"statusCode": 405, "headers": {"Allow": "GET, POST"}, "body": ""}

    # Single use: claim the code first so a double tap can't confirm twice.
    try:
        incidents.update_item(Key=ack_key, UpdateExpression="SET used_at = :t",
                              ConditionExpression="attribute_not_exists(used_at)",
                              ExpressionAttributeValues={":t": now})
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        return _page(200, lang, f"<p>{html.escape(ui.get('already', 'Already confirmed.'))}</p>")

    late = False
    try:
        sfn.send_task_success(taskToken=item["task_token"], output=json.dumps(
            {"acked_by": item["contact_id"], "role": item["role"], "level": int(item["level"]), "at": now}))
    except ClientError as e:
        if e.response["Error"]["Code"] not in LATE_ERRORS:
            incidents.update_item(Key=ack_key, UpdateExpression="REMOVE used_at")  # let them retry
            raise
        late = True  # rung already timed out, or a colleague confirmed first

    try:
        incidents.update_item(
            Key={"pk": item["incident_pk"], "sk": item["incident_sk"]},
            UpdateExpression=("SET #st = :ack, acknowledged_by = :who, acknowledged_role = :role, "
                              "acknowledged_at = :t, gsi1sk = :g"),
            ConditionExpression="#st = :open",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={":ack": "ACKNOWLEDGED", ":open": "OPEN", ":who": item["contact_id"],
                                       ":role": item["role"], ":t": now,
                                       ":g": f"STATUS#ACKNOWLEDGED#{int(item['first_seen']):013d}"},
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise  # else already acknowledged by someone else

    log.info("ack %s by %s (%s) late=%s", item["incident_id"], item["contact_id"], item["role"], late)
    msg = ui.get("late", "Recorded.") if late else ui.get("confirmed", "Confirmed.")
    return _page(200, lang, f"<p>{html.escape(msg)}</p>")


@lru_cache(maxsize=1)
def _deps():
    region = os.environ.get("AWS_REGION")
    return (
        boto3.resource("dynamodb", region_name=region).Table(os.environ["INCIDENTS_TABLE"]),
        boto3.client("stepfunctions", region_name=region),
        VoiceClips(boto3.client("polly", region_name=region), boto3.client("s3", region_name=region),
                   os.environ["VOICE_BUCKET"]),
    )


_secret_cache: tuple[float, str] | None = None


def expected_origin_secret() -> str | None:
    """Fails closed: if the check is required and the secret can't be read, every request is refused."""
    global _secret_cache
    if os.environ.get("REQUIRE_ORIGIN_VERIFY", "false").lower() != "true":
        return None
    if _secret_cache and _secret_cache[0] > time.monotonic():
        return _secret_cache[1]
    value = boto3.client("secretsmanager").get_secret_value(
        SecretId=os.environ["ORIGIN_VERIFY_SECRET_ID"])["SecretString"]
    _secret_cache = (time.monotonic() + 300, value)  # picks up a rotated secret within 5 min
    return value


def handler(event, _context):
    incidents, sfn, voice = _deps()
    return respond(event, incidents, sfn, voice, expected_origin_secret())
