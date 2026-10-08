"""Who gets paged, in what order, and how long each rung has to acknowledge."""

# severity -> [(role, ack timeout in seconds)]. 0 = inform only, don't wait for an ack.
# Roles follow Indian Railways P.Way hierarchy: gangman/keyman on the beat → Section
# Engineer (SSE/P.Way) → Divisional Engineer (DEN).
ESCALATION_POLICY: dict[int, list[tuple[str, int]]] = {
    1: [("GANGMAN", 600), ("SSE", 600), ("DEN", 900)],
    2: [("GANGMAN", 1800), ("SSE", 3600)],
    3: [("SSE", 0)],
    # P4 is dashboard-only.
}


def _covers(contact: dict, km: float | None) -> bool:
    lo, hi = contact.get("beat_km_from"), contact.get("beat_km_to")
    if km is None or lo is None or hi is None:
        return False
    return float(lo) <= km < float(hi)


def build_ladder(contacts: list[dict], severity: int, km: float | None,
                 timeout_override_s: int | None = None) -> list[dict]:
    """One rung per role in the policy that has at least one reachable contact.

    Gangmen are matched to the segment by their beat (km range); if nobody's beat covers it,
    every gangman in the division is paged rather than nobody.
    `timeout_override_s` shortens every waiting rung — used to make escalation visible in demos.
    """
    ladder = []
    for role, timeout in ESCALATION_POLICY.get(severity, []):
        in_role = [c for c in contacts if c.get("role") == role]
        people = [c for c in in_role if _covers(c, km)] or in_role
        if not people:
            continue
        if timeout and timeout_override_s:
            timeout = timeout_override_s
        ladder.append({
            "role": role,
            "ack_timeout_s": timeout,
            "contacts": [
                {"contact_id": c["contact_id"], "name": c.get("name", c["contact_id"]),
                 "phone": c.get("phone"), "lang": c.get("lang", "en")}
                for c in people
            ],
        })
    return ladder
