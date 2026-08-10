"""Commerce session-token bridge + S2S user-summary bridge.

Two surfaces:

  * GET  /commerce/session-token — the weespas frontend calls this after login to obtain a
    commerce-scoped RS256 token, then talks to commerce (:8003) directly with that bearer —
    same pattern as the InSAR deep-link (routers/insar.py:/session-token). Weespas mints;
    commerce verifies with the public key only. No weespas request can be authenticated by a
    commerce token (the scope is rejected in get_current_user — see auth_service
    ._FOREIGN_SCOPES).

  * POST /commerce/users/lookup  — the COMMERCE → WEESPAS direction, added for the seller-
    console Viewing Card (§8 Chunk C+). Commerce takes a list of viewer uuids from
    shop_view_events, calls this endpoint, and gets back {display_name, avatar_url, phone}
    for each existing user. NO user auth involved (commerce is the caller, not a browser);
    S2S auth is a shared-secret header (X-Service-Secret). Fail-closed: if the secret is
    unset in config, the endpoint 503s so a dev env can't silently accept a wrong secret.

The commerce → weespas call is deliberately narrow: ONE endpoint, ONE shape, ONE secret.
Adding more S2S surfaces later would mean opening this pattern up; for now it stays scoped
tightly around the humanized viewer card.
"""
import hmac
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from PE.weespas.core.config import settings
from PE.weespas.core.database import get_db
from PE.weespas.models.user import User, UserRole
from PE.weespas.services.auth_service import create_commerce_token, get_current_user

router = APIRouter(prefix="/commerce", tags=["commerce"])


class CommerceSession(BaseModel):
    token: str
    commerce_url: str


@router.get("/session-token", response_model=CommerceSession)
def session_token(user: User = Depends(get_current_user)) -> CommerceSession:
    """Mint a commerce-scoped token for the signed-in user."""
    role_value = user.role.value if isinstance(user.role, UserRole) else (user.role or "user")
    # Least-privilege: the read feed + the seller write path (shops/listings/POS stock). Money
    # /settlement scopes are NOT granted here — they land behind require_settlement_principal
    # (the Redis denylist hook) when that path ships.
    token = create_commerce_token(
        user.id, role_value, scopes=["read:feed", "create:trades"], name=user.name
    )
    return CommerceSession(token=token, commerce_url=settings.commerce_public_url)


# ---------------------------------------------------------------------------
# S2S: commerce → weespas user-summary bridge (§8 Chunk C+)
# ---------------------------------------------------------------------------

# Cap on how many uuids one call may lookup. A live-viewers page shows a small handful of
# viewers; 100 is enough headroom for a "top shop" case AND small enough that a rogue caller
# can't turn this into a bulk exfiltration primitive. Mirrors the shops-on-map link cap.
_MAX_LOOKUP_UUIDS = 100


class UserSummary(BaseModel):
    """One viewer's summary. `phone` is present unconditionally — the caller (commerce) is
    responsible for deciding which requesting seller sees it (followers-only rule per the
    Chunk C+ design). Bridge is stateless."""
    uuid: str
    display_name: str
    avatar_url: str | None = None
    phone: str | None = None


class UserLookupRequest(BaseModel):
    uuids: list[str] = Field(min_length=1, max_length=_MAX_LOOKUP_UUIDS)


class UserLookupResponse(BaseModel):
    items: list[UserSummary]


def _require_service_secret(x_service_secret: str | None = Header(default=None)) -> None:
    """Constant-time verification of the S2S shared secret. Fails CLOSED:
      * unset config secret ⇒ 503 (the endpoint is disabled by default)
      * missing header       ⇒ 401
      * mismatched header    ⇒ 401
    """
    configured = settings.commerce_users_lookup_secret
    if not configured:
        raise HTTPException(status_code=503, detail="Service bridge not configured")
    if not x_service_secret or not hmac.compare_digest(configured, x_service_secret):
        raise HTTPException(status_code=401, detail="Invalid service credentials")


@router.post(
    "/users/lookup",
    response_model=UserLookupResponse,
    dependencies=[Depends(_require_service_secret)],
)
def users_lookup(
    body: UserLookupRequest,
    db: Session = Depends(get_db),
) -> UserLookupResponse:
    """Return summaries for the requested user uuids. Missing users are simply absent from
    the response — the caller de-duplicates against its own input. One indexed IN() query.

    De-duplicates the input list before the query (a caller that accidentally sends the same
    uuid twice gets one row back, not two)."""
    unique_uuids = list(dict.fromkeys(body.uuids))   # order-preserving de-dup
    if not unique_uuids:
        return UserLookupResponse(items=[])
    rows = db.query(User).filter(User.id.in_(unique_uuids)).all()
    return UserLookupResponse(
        items=[
            UserSummary(
                uuid=u.id,
                display_name=u.name,
                avatar_url=u.avatar,
                # Phone is unconditionally included; commerce applies the followers-only rule.
                # See routers/commerce.py's docstring for the full contract.
                phone=u.phone,
            )
            for u in rows
        ],
    )
