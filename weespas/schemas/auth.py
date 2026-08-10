from pydantic import BaseModel, Field, validator
from datetime import datetime
from typing import Optional, List
import re

ALLOWED_ROLES = {"user", "agent", "staff", "admin"}


# ─── Phone normalization ─────────────────────────────────────────────
# Every phone that crosses the API boundary is collapsed to canonical
# E.164-ish form: "+254XXXXXXXXX". One canonical value in the DB means
# the unique index and every lookup agree on what "the same phone" is.
# Without this, "0712345678" / "+254712345678" / "254712345678" become
# three different rows for the same human — register says "already
# registered" while login says "no account found." See bug-fix turn.
#
# Pure-string ops (no regex compile per call after the module-level
# bind), branch-light: handles the four formats Kenyan users actually
# type. Returns the raw value unchanged when it can't recognize the
# shape so callers can surface a clean "Invalid phone" error instead of
# silently storing garbage.
_PHONE_STRIP_RE = re.compile(r"[\s\-()]")


def normalize_phone(raw: str) -> str:
    """Canonicalize a Kenyan phone to '+254XXXXXXXXX'. Raise ValueError
    on shapes we can't recognize so the validator surfaces a 422 instead
    of writing an unindexable value."""
    if not raw:
        raise ValueError("Phone number is required")
    cleaned = _PHONE_STRIP_RE.sub("", raw)
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]
    # Local 0-prefixed (10 digits, e.g. "0712345678") → swap leading 0 for "254".
    if cleaned.startswith("0") and len(cleaned) == 10 and cleaned.isdigit():
        return "+254" + cleaned[1:]
    # Already country-coded (12 digits, "254..."): straight prefix with +.
    if cleaned.startswith("254") and len(cleaned) == 12 and cleaned.isdigit():
        return "+" + cleaned
    # 9-digit subscriber number (e.g. "712345678") — also a common paste.
    if len(cleaned) == 9 and cleaned.isdigit():
        return "+254" + cleaned
    raise ValueError("Invalid phone number format")


class RegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    email: str = Field(..., max_length=255)
    phone: str = Field(..., max_length=20)
    password: str = Field(..., min_length=6, max_length=128)

    @validator("email")
    def validate_email(cls, v):
        pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        if not re.match(pattern, v):
            raise ValueError("Invalid email format")
        return v.lower()

    @validator("phone")
    def validate_phone(cls, v):
        return normalize_phone(v)


class LoginRequest(BaseModel):
    email: Optional[str] = None
    password: Optional[str] = None
    phone: Optional[str] = None

    @validator("email", pre=True)
    def normalize_email(cls, v):
        return v.lower() if v else v

    @validator("phone")
    def _normalize_phone(cls, v):
        return normalize_phone(v) if v else v


class OtpVerifyRequest(BaseModel):
    phone: str
    otp: str = Field(..., min_length=6, max_length=6)

    @validator("phone")
    def _normalize_phone(cls, v):
        return normalize_phone(v)


class ResendOtpRequest(BaseModel):
    phone: str

    @validator("phone")
    def _normalize_phone(cls, v):
        return normalize_phone(v)


class UserResponse(BaseModel):
    id: str
    name: str
    email: str
    phone: str
    avatar: Optional[str] = None
    role: str = "user"
    roles: List[str] = []
    agent_id: Optional[str] = None
    is_public_profile: bool = False
    # Phase 6 — notification prefs
    notify_inquiries_sms: bool = True
    notify_inquiries_email: bool = False
    notify_digest_email: bool = False
    notify_push: bool = False
    # Phase 8 — search defaults
    default_radius_km: Optional[int] = 10
    preferred_listing_type: Optional[str] = None  # 'rent' | 'sale'
    language: Optional[str] = 'en'  # 'en' | 'sw'
    # Bio is sourced from agents.bio (Text, nullable) when the user is
    # linked to an Agent profile; None otherwise. Populated by the GET
    # /auth/me handler in routers/auth.py — there is no users.bio column.
    bio: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class UserUpdateRequest(BaseModel):
    """Partial update for the authenticated user. All fields optional —
    only provided ones are applied. New phases extend this schema with
    additional fields (notification prefs, search defaults, etc.)."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    avatar: Optional[str] = Field(None, max_length=500)
    is_public_profile: Optional[bool] = None
    # Phase 6 — notification preferences
    notify_inquiries_sms: Optional[bool] = None
    notify_inquiries_email: Optional[bool] = None
    notify_digest_email: Optional[bool] = None
    notify_push: Optional[bool] = None
    # Phase 8 — search defaults
    default_radius_km: Optional[int] = Field(None, ge=1, le=200)
    preferred_listing_type: Optional[str] = Field(None, max_length=16)
    language: Optional[str] = Field(None, max_length=8)

    @validator("preferred_listing_type")
    def _vlt(cls, v):
        if v is None:
            return v
        if v not in {"rent", "sale"}:
            raise ValueError("preferred_listing_type must be 'rent' or 'sale'")
        return v

    @validator("language")
    def _vlang(cls, v):
        if v is None:
            return v
        if v not in {"en", "sw"}:
            raise ValueError("language must be 'en' or 'sw'")
        return v


class UserPublicResponse(BaseModel):
    """Privacy-aware response: hides email/phone unless user's profile is public."""
    id: str
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    avatar: Optional[str] = None
    role: str = "user"
    roles: List[str] = []
    agent_id: Optional[str] = None
    is_public_profile: bool = False
    created_at: datetime

    class Config:
        from_attributes = True


class UserAdminResponse(BaseModel):
    """Full user details visible to admin/staff (everything except password)."""
    id: str
    name: str
    email: str
    phone: str
    avatar: Optional[str] = None
    role: str = "user"
    roles: List[str] = []
    agent_id: Optional[str] = None
    is_public_profile: bool = False
    is_active: bool = True
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class RoleAssignRequest(BaseModel):
    role: str = Field(..., description="Role to assign: user, agent, staff, admin")

    @validator("role")
    def validate_role(cls, v):
        if v not in ALLOWED_ROLES:
            raise ValueError(f"Role must be one of: {', '.join(sorted(ALLOWED_ROLES))}")
        return v


class RolesAssignRequest(BaseModel):
    roles: List[str] = Field(..., description="Full list of roles to assign (replace semantics)")

    @validator("roles")
    def validate_roles(cls, v):
        if not v:
            raise ValueError("At least one role is required")
        unknown = [r for r in v if r not in ALLOWED_ROLES]
        if unknown:
            raise ValueError(
                f"Unknown role(s): {', '.join(unknown)}. Allowed: {', '.join(sorted(ALLOWED_ROLES))}"
            )
        # de-dupe while preserving order
        seen = set()
        deduped = []
        for r in v:
            if r not in seen:
                seen.add(r)
                deduped.append(r)
        return deduped


class DeletionRequestCreate(BaseModel):
    target_user_id: str = Field(..., description="ID of user/agent to delete")
    reason: str = Field(..., min_length=10, max_length=1000, description="Reason for deletion")


class DeletionRequestResponse(BaseModel):
    id: str
    target_user_id: Optional[str] = None
    target_user_name: Optional[str] = None
    requested_by_id: Optional[str] = None
    requested_by_name: Optional[str] = None
    reason: str
    status: str = "pending"
    reviewed_by_id: Optional[str] = None
    reviewed_by_name: Optional[str] = None
    review_note: Optional[str] = None
    created_at: datetime
    reviewed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class DeletionReviewRequest(BaseModel):
    status: str = Field(..., description="approved or rejected")
    review_note: Optional[str] = Field(None, max_length=500)

    @validator("status")
    def validate_status(cls, v):
        if v not in {"approved", "rejected"}:
            raise ValueError("Status must be 'approved' or 'rejected'")
        return v


class PaginatedUserResponse(BaseModel):
    total: int
    skip: int
    limit: int
    items: list


class AuthResponse(BaseModel):
    token: str
    user: UserResponse
