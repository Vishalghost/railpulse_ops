"""EB worker entrypoint. sqsd POSTs each SQS message body to /process:
2xx → message deleted; anything else → retried, then moved to the DLQ after 5 receives."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import Depends, FastAPI, Header

from .config import Settings
from .identity import log_aws_identity
from .processor import Processor, build_processor
from .schema import TelemetryMessage

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("railpulse.worker")

@asynccontextmanager
async def lifespan(_app: FastAPI):
    await asyncio.to_thread(log_aws_identity)  # log-only: never blocks startup
    yield


app = FastAPI(title="RailPulse detection worker", lifespan=lifespan)


@lru_cache(maxsize=1)
def get_processor() -> Processor:
    return build_processor(Settings.from_env())


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/process")
def process(
    msg: TelemetryMessage,
    processor: Processor = Depends(get_processor),
    receive_count: int | None = Header(default=None, alias="X-Aws-Sqsd-Receive-Count"),
) -> dict:
    result = processor.process(msg)
    if result.findings:
        log.info(json.dumps({
            "event": "findings",
            "sensor_id": msg.sensor_id,
            "segment_id": msg.segment_id,
            "receive_count": receive_count,
            "findings": [{"code": f.code, "severity": f"P{int(f.severity)}", "detail": f.detail}
                         for f in result.findings],
            "incidents": [{"id": c.item["incident_id"], "change": c.kind} for c in result.changes],
        }))
    return {
        "findings": len(result.findings),
        "incidents": [{"id": c.item["incident_id"], "change": c.kind, "severity": int(c.item["severity"])}
                      for c in result.changes],
    }
