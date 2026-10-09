"""Alert wording. English is the source; Amazon Translate renders it per contact.

IDs, km markers and URLs are appended *after* translation so they're never mangled.
"""

DIVISION_NAMES = {"DLI": "Delhi", "BB": "Mumbai CSMT", "BCT": "Mumbai Central", "MAS": "Chennai",
                  "HWH": "Howrah", "SC": "Secunderabad", "SBC": "Bengaluru", "LKO": "Lucknow"}

TYPE_TEXT = {
    "CRACK_SUSPECTED": "Suspected rail crack",
    "IMPACT_ANOMALY": "Abnormal wheel impact on the rail",
    "VIBRATION_ANOMALY": "Abnormal track vibration",
    "BUCKLING_RISK": "Very high rail temperature with buckling risk",
    "COLD_STRESS": "Very low rail temperature with fracture risk",
    "SWITCH_DEGRADED": "Point machine is degrading",
    "SWITCH_FAILURE": "Point machine has failed",
    "SENSOR_FAULT": "Track sensor fault",
    "SEGMENT_SILENT": "All track sensors stopped reporting, possible cable cut or theft",
    "SENSOR_SILENT": "A track sensor stopped reporting",
    "LOW_BATTERY": "Track sensor battery is low",
}

ACTION_TEXT = {
    1: "Protect the line and inspect immediately.",
    2: "Inspect during this shift.",
    3: "Inspect within 24 hours.",
}

UI_TEXT = {
    "button": "I have seen this alert and I am going to the site",
    "confirmed": "Confirmed. Thank you, your response is recorded.",
    "already": "This alert was already confirmed.",
    "late": "Recorded. The alert had already been escalated to your senior.",
}


def compose(incident: dict) -> str:
    """The translatable core sentence(s)."""
    sev = int(incident["severity"])
    what = TYPE_TEXT.get(incident["type"], "Track anomaly")
    division = DIVISION_NAMES.get(incident["division"], incident["division"])
    return f"Railway alert. {what} in {division} division. {ACTION_TEXT.get(sev, '')}".strip()


def sms_body(core: str, incident: dict, ack_url: str | None) -> str:
    sev = int(incident["severity"])
    km = incident.get("km_from")
    place = ", ".join(p for p in (incident.get("line_name"), incident.get("section")) if p)
    where = (f"{place}, " if place else "") + f"{incident['segment_id']}" + (f" km {float(km):.1f}" if km is not None else "")
    tail = f" ACK: {ack_url}" if ack_url else ""
    return f"RailPulse P{sev} | {core} | {where}{tail}"


class Translator:
    """Caches per (text, lang) — a crack pages the same sentence to many people."""

    def __init__(self, client) -> None:
        self._client = client
        self._cache: dict[tuple[str, str], str] = {}

    def __call__(self, text: str, lang: str) -> str:
        if lang == "en":
            return text
        key = (text, lang)
        if key not in self._cache:
            resp = self._client.translate_text(Text=text, SourceLanguageCode="en", TargetLanguageCode=lang)
            self._cache[key] = resp["TranslatedText"]
        return self._cache[key]
