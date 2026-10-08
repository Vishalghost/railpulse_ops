import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    region: str
    incidents_table: str
    assets_table: str
    telemetry_table: str
    user_pool_id: str
    cognito_domain: str
    # The app client ID is read from SSM at runtime (USER_POOL_CLIENT_ID overrides, e.g. locally):
    # the client's callback URL depends on CloudFront, which depends on this environment, so
    # passing the ID as an env var would be a CloudFormation dependency cycle.
    client_id_param: str | None = None
    client_id: str | None = None
    map_key_name: str | None = None
    cors_origins: list[str] = field(default_factory=list)
    live_poll_s: float = 3.0

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        return cls(
            region=env.get("RAILPULSE_REGION", "ap-south-1"),
            incidents_table=env["INCIDENTS_TABLE"],
            assets_table=env["ASSETS_TABLE"],
            telemetry_table=env["TELEMETRY_TABLE"],
            user_pool_id=env["USER_POOL_ID"],
            cognito_domain=env["COGNITO_DOMAIN"],
            client_id_param=env.get("USER_POOL_CLIENT_ID_PARAM") or None,
            client_id=env.get("USER_POOL_CLIENT_ID") or None,
            map_key_name=env.get("MAP_API_KEY_NAME") or None,
            cors_origins=[o for o in env.get("CORS_ORIGINS", "").split(",") if o],
            live_poll_s=float(env.get("LIVE_POLL_SECONDS", "3")),
        )
