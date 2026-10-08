"""Cognito access-token verification and role/division scoping.

Users carry two kinds of Cognito group:
  role      Admin | DEN | SSE | Gangman
  division  DIV_DLI | DIV_MAS | DIV_BB      (Admin sees every division)
Groups ride in the access token's `cognito:groups` claim, so no user lookup per request.
"""

from dataclasses import dataclass

import jwt

ROLES = ("Admin", "DEN", "SSE", "Gangman")
DIVISION_PREFIX = "DIV_"
CAN_RESOLVE = frozenset({"Admin", "DEN", "SSE"})


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class Principal:
    sub: str
    username: str
    roles: frozenset[str]
    divisions: frozenset[str]

    @property
    def is_admin(self) -> bool:
        return "Admin" in self.roles

    def can_view(self, division: str) -> bool:
        return self.is_admin or division in self.divisions

    @property
    def can_resolve(self) -> bool:
        return bool(self.roles & CAN_RESOLVE)


class TokenVerifier:
    def __init__(self, region: str, user_pool_id: str, client_id: str, jwks_client=None) -> None:
        self.issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
        self.client_id = client_id
        self._jwks = jwks_client or jwt.PyJWKClient(f"{self.issuer}/.well-known/jwks.json", cache_keys=True)

    def verify(self, token: str) -> Principal:
        try:
            key = self._jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token, key, algorithms=["RS256"], issuer=self.issuer,
                options={"require": ["exp", "iss", "sub", "token_use"], "verify_aud": False},
            )
        except jwt.PyJWTError as e:
            raise AuthError(str(e)) from e
        # Access tokens have no `aud`; Cognito puts the app client in `client_id` instead.
        if claims.get("token_use") != "access" or claims.get("client_id") != self.client_id:
            raise AuthError("not an access token for this app client")

        groups = set(claims.get("cognito:groups", []))
        return Principal(
            sub=claims["sub"],
            username=claims.get("username", claims["sub"]),
            roles=frozenset(g for g in groups if g in ROLES),
            divisions=frozenset(g[len(DIVISION_PREFIX):] for g in groups if g.startswith(DIVISION_PREFIX)),
        )
