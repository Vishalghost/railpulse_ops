"""Delivery channels. Each returns a result dict instead of raising, so one bad number
doesn't stop the rest of a rung from being paged."""

import json
from decimal import Decimal

from botocore.exceptions import ClientError

# Polly has Indian voices for Hindi and Indian English only; other languages fall back to en-IN.
VOICES = {
    "hi": ("Kajal", "hi-IN"),
    "en": ("Kajal", "en-IN"),
}


def json_default(o):
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    if isinstance(o, set):
        return sorted(o)
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")


class SmsSender:
    """AWS End User Messaging SMS (pinpoint-sms-voice-v2).

    mode: "off" (skip), "dry_run" (API validates, nothing is sent), "live".
    India requires DLT registration: entity ID + template ID ride along as country parameters.
    """

    def __init__(self, client, mode: str, origination_id: str | None = None,
                 in_entity_id: str | None = None, in_template_id: str | None = None) -> None:
        self._client = client
        self._mode = mode
        self._origination_id = origination_id
        self._dlt = {"IN_ENTITY_ID": in_entity_id, "IN_TEMPLATE_ID": in_template_id} \
            if in_entity_id and in_template_id else None

    def send(self, phone: str | None, body: str) -> dict:
        if not phone or self._mode == "off":
            return {"channel": "sms", "status": "skipped"}
        kwargs = {
            "DestinationPhoneNumber": phone,
            "MessageBody": body,
            "MessageType": "TRANSACTIONAL",
            "DryRun": self._mode == "dry_run",
        }
        if self._origination_id:
            kwargs["OriginationIdentity"] = self._origination_id
        if self._dlt and phone.startswith("+91"):
            kwargs["DestinationCountryParameters"] = self._dlt
        try:
            resp = self._client.send_text_message(**kwargs)
        except ClientError as e:
            return {"channel": "sms", "status": "failed", "error": e.response["Error"]["Code"]}
        return {"channel": "sms", "status": "dry_run" if kwargs["DryRun"] else "sent",
                "message_id": resp.get("MessageId")}


class TopicPublisher:
    """SNS fan-out: ops email/SMS subscribers and any downstream integrations."""

    def __init__(self, client, topic_arn: str) -> None:
        self._client = client
        self._topic_arn = topic_arn

    def publish(self, subject: str, message: str, incident: dict) -> dict:
        try:
            resp = self._client.publish(
                TopicArn=self._topic_arn,
                Subject=subject[:100],
                Message=message,
                MessageAttributes={
                    "division": {"DataType": "String", "StringValue": str(incident["division"])},
                    "severity": {"DataType": "Number", "StringValue": str(int(incident["severity"]))},
                    "type": {"DataType": "String", "StringValue": str(incident["type"])},
                },
            )
        except ClientError as e:
            return {"channel": "sns", "status": "failed", "error": e.response["Error"]["Code"]}
        return {"channel": "sns", "status": "sent", "message_id": resp.get("MessageId")}


class VoiceClips:
    """Amazon Polly → MP3 in S3, played from the ack page (works on any phone with a browser)."""

    def __init__(self, polly, s3, bucket: str) -> None:
        self._polly = polly
        self._s3 = s3
        self._bucket = bucket

    def render(self, key: str, text: str, lang: str, fallback_text: str) -> str | None:
        voice, lang_code = VOICES.get(lang, VOICES["en"])
        spoken = text if lang in VOICES else fallback_text
        try:
            audio = self._polly.synthesize_speech(
                Text=spoken, VoiceId=voice, LanguageCode=lang_code, Engine="neural", OutputFormat="mp3"
            )["AudioStream"].read()
            self._s3.put_object(Bucket=self._bucket, Key=key, Body=audio, ContentType="audio/mpeg")
        except ClientError:
            return None  # an alert without audio is still an alert
        return key

    def presign(self, key: str, expires_s: int = 3600) -> str:
        return self._s3.generate_presigned_url(
            "get_object", Params={"Bucket": self._bucket, "Key": key}, ExpiresIn=expires_s
        )


def dumps(obj) -> str:
    return json.dumps(obj, default=json_default, separators=(",", ":"))
