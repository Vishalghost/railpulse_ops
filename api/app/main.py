import asyncio
import json
import os
import re
import time
from contextlib import asynccontextmanager
from functools import cached_property, lru_cache

import boto3
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from .auth import AuthError, Principal, TokenVerifier
from .config import Settings
from .identity import log_aws_identity
from .live import LiveHub
from .repo import STATUSES, Conflict, NotFound, Repo, public

ALL_DIVISIONS = tuple(d for d in os.environ.get("DIVISIONS", "DLI,BB,BCT,MAS,HWH,SC,SBC,LKO").split(",") if d)
DIVISION_RE = r"^[A-Z]{2,4}$"
SEGMENT_RE = re.compile(r"^SEG-([A-Z]{2,4})-\d{3}$")
SSE_PING_S = 15  # under CloudFront's 30 s origin read timeout


class Services:
    """Process-wide singletons; tests replace the whole object via dependency_overrides."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        ddb = boto3.resource("dynamodb", region_name=settings.region)
        self.repo = Repo(ddb.Table(settings.incidents_table), ddb.Table(settings.assets_table),
                         ddb.Table(settings.telemetry_table))
        self.hub = LiveHub(self.repo.active_incidents, interval_s=settings.live_poll_s)
        self._map_style: tuple[float, str | None] | None = None

    @cached_property
    def client_id(self) -> str:
        if self.settings.client_id:
            return self.settings.client_id
        ssm = boto3.client("ssm", region_name=self.settings.region)
        return ssm.get_parameter(Name=self.settings.client_id_param)["Parameter"]["Value"]

    @cached_property
    def verifier(self) -> TokenVerifier:
        return TokenVerifier(self.settings.region, self.settings.user_pool_id, self.client_id)

    def map_style_url(self) -> str | None:
        """Amazon Location (Maps v2) style URL. The API key is referer-restricted to the dashboard."""
        if not self.settings.map_key_name:
            return None
        if self._map_style and self._map_style[0] > time.monotonic():
            return self._map_style[1]
        try:
            key = boto3.client("location", region_name=self.settings.region) \
                .describe_key(KeyName=self.settings.map_key_name)["Key"]
            # Dark monochrome basemap for the control-room view; India's official borders.
            url = (f"https://maps.geo.{self.settings.region}.amazonaws.com/v2/styles/Monochrome/descriptor"
                   f"?color-scheme=Dark&political-view=IND&key={key}")
        except Exception:
            url = None  # dashboard falls back to a schematic view
        self._map_style = (time.monotonic() + 3600, url)
        return url

    def public_config(self) -> dict:
        return {
            "region": self.settings.region,
            "userPoolId": self.settings.user_pool_id,
            "clientId": self.client_id,
            "cognitoDomain": self.settings.cognito_domain,
            "mapStyleUrl": self.map_style_url(),
        }


@lru_cache(maxsize=1)
def get_services() -> Services:
    return Services(Settings.from_env())


_bearer = HTTPBearer(auto_error=False)


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
                 svc: Services = Depends(get_services)) -> Principal:
    if creds is None:
        raise HTTPException(401, "missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    try:
        return svc.verifier.verify(creds.credentials)
    except AuthError:
        raise HTTPException(401, "invalid token", headers={"WWW-Authenticate": "Bearer"}) from None


def _require_division(user: Principal, division: str) -> None:
    if not user.can_view(division):
        raise HTTPException(403, f"no access to division {division}")


def _load_incident(svc: Services, user: Principal, incident_id: str) -> dict:
    try:
        incident = svc.repo.get_incident(incident_id)
    except NotFound:
        raise HTTPException(404, "incident not found") from None
    _require_division(user, incident["division"])
    return incident


# --- routes -------------------------------------------------------------------------------
api = APIRouter(prefix="/api")


@api.get("/health")
def health() -> dict:
    return {"status": "ok"}


@api.get("/config")
def config(svc: Services = Depends(get_services)) -> dict:
    """Public bootstrap for the static dashboard, so one build runs in every environment."""
    return svc.public_config()


@api.get("/me")
def me(user: Principal = Depends(current_user)) -> dict:
    divisions = ALL_DIVISIONS if user.is_admin else sorted(user.divisions)
    return {"username": user.username, "roles": sorted(user.roles), "divisions": list(divisions),
            "canResolve": user.can_resolve}


@api.get("/divisions/{division}/incidents")
def list_incidents(division: str, status: str = Query("OPEN"), limit: int = Query(100, ge=1, le=500),
                   user: Principal = Depends(current_user), svc: Services = Depends(get_services)) -> dict:
    _require_division(user, division)
    if status not in STATUSES:
        raise HTTPException(422, f"status must be one of {STATUSES}")
    return {"items": svc.repo.list_incidents(division, status, limit)}


@api.get("/divisions/{division}/map")
def division_map(division: str, user: Principal = Depends(current_user),
                 svc: Services = Depends(get_services)) -> dict:
    """Segments with the worst active severity on each — one call renders the whole map."""
    _require_division(user, division)
    worst: dict[str, dict] = {}
    for inc in svc.repo.active_incidents(division):
        seg = inc["segment_id"]
        if seg not in worst or int(inc["severity"]) < int(worst[seg]["severity"]):
            worst[seg] = inc
    segments = []
    for seg in sorted(svc.repo.segments(division), key=lambda s: s["segment_id"]):
        top = worst.get(seg["segment_id"])
        segments.append({**seg, "worst_severity": int(top["severity"]) if top else None,
                         "worst_incident": top["incident_id"] if top else None})
    return {"division": division, "segments": segments}


@api.get("/divisions/{division}/sensor-health")
def sensor_health(division: str, user: Principal = Depends(current_user),
                  svc: Services = Depends(get_services)) -> dict:
    _require_division(user, division)
    return {"health": svc.repo.sensor_health(division)}  # null until the first heartbeat run


@api.get("/incidents/{incident_id}")
def get_incident(incident_id: str, user: Principal = Depends(current_user),
                 svc: Services = Depends(get_services)) -> dict:
    incident = _load_incident(svc, user, incident_id)
    try:
        segment = svc.repo.segment(incident["segment_id"])
    except NotFound:
        segment = None
    return {"incident": public(incident), "timeline": svc.repo.timeline(incident), "segment": segment}


@api.post("/incidents/{incident_id}/acknowledge")
def acknowledge(incident_id: str, user: Principal = Depends(current_user),
                svc: Services = Depends(get_services)) -> dict:
    incident = _load_incident(svc, user, incident_id)
    try:
        return {"incident": public(svc.repo.acknowledge(incident, user.username))}
    except Conflict as e:
        raise HTTPException(409, str(e)) from None


class ResolveBody(BaseModel):
    note: str | None = Field(default=None, max_length=500)


@api.post("/incidents/{incident_id}/resolve")
def resolve(incident_id: str, body: ResolveBody, user: Principal = Depends(current_user),
            svc: Services = Depends(get_services)) -> dict:
    if not user.can_resolve:
        raise HTTPException(403, "only SSE, DEN or Admin can resolve incidents")
    incident = _load_incident(svc, user, incident_id)
    try:
        return {"incident": public(svc.repo.resolve(incident, user.username, body.note))}
    except Conflict as e:
        raise HTTPException(409, str(e)) from None


@api.get("/segments/{segment_id}/telemetry")
def telemetry(segment_id: str, hours: float = Query(6, gt=0, le=72), limit: int = Query(720, ge=1, le=2000),
              user: Principal = Depends(current_user), svc: Services = Depends(get_services)) -> dict:
    m = SEGMENT_RE.match(segment_id)
    if not m:
        raise HTTPException(404, "segment not found")
    _require_division(user, m.group(1))
    to_ms = int(time.time() * 1000)
    from_ms = to_ms - int(hours * 3_600_000)
    sensors = svc.repo.segment_sensors(segment_id)
    return {
        "segment_id": segment_id,
        "from": from_ms,
        "to": to_ms,
        "sensors": [{**s, "points": svc.repo.telemetry_for(s["sensor_id"], from_ms, to_ms, limit)}
                    for s in sensors],
    }


@api.get("/stream/incidents")
async def stream_incidents(division: str = Query(..., pattern=DIVISION_RE),
                           user: Principal = Depends(current_user),
                           svc: Services = Depends(get_services)) -> StreamingResponse:
    _require_division(user, division)
    queue = svc.hub.subscribe(division)

    async def events():
        try:
            yield "retry: 5000\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=SSE_PING_S)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"  # keeps CloudFront/ALB from closing an idle stream
                    continue
                payload = json.dumps(jsonable_encoder({**ev, "incident": public(ev["incident"])}))
                yield f"event: incident\ndata: {payload}\n\n"
        finally:
            svc.hub.unsubscribe(division, queue)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await asyncio.to_thread(log_aws_identity)  # log-only: never blocks startup
    yield


app = FastAPI(title="RailPulse API", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)
app.include_router(api)

_origins = [o for o in os.environ.get("CORS_ORIGINS", "").split(",") if o]
if _origins:  # only needed for `next dev` on localhost; in AWS the dashboard is same-origin
    app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["GET", "POST"],
                       allow_headers=["Authorization", "Content-Type"])
