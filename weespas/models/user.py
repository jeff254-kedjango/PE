import enum
from sqlalchemy import Enum

from sqlalchemy import Column, String, DateTime, Boolean, Text, ForeignKey, Integer
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from PE.weespas.core.database import Base


class UserRole(enum.Enum):
    USER = "user"
    AGENT = "agent"
    STAFF = "staff"
    ADMIN = "admin"
    # ─── P4a: InSAR-integration roles ───────────────────────────────────────
    # These are RELATIONSHIPS-to-buildings capabilities, granted ONLY via the
    # `user_roles` VARCHAR table (use `User.has_role(...)` / the multi-role API),
    # NEVER written to the native-enum `users.role` column below. Writing one of
    # these to `users.role` would require an `ALTER TYPE ... ADD VALUE` on the PG
    # enum (a transaction hazard on older PG) — deliberately avoided. The legacy
    # primary `role` column stays one of user/agent/staff/admin.
    PROFESSIONAL = "professional"      # civil/geo engineer — can certify buildings, deep read access
    PROPERTY_OWNER = "property_owner"  # owns building(s) — monitors + owner-tier alerts
    TENANT = "tenant"                  # rents a unit — tenant-tier alerts
    AUTHORITY = "authority"            # responsible for a jurisdiction — authority-tier alerts

    @classmethod
    def primary_roles(cls) -> set["UserRole"]:
        """The roles valid for the native-enum `users.role` column (legacy set)."""
        return {cls.USER, cls.AGENT, cls.STAFF, cls.ADMIN}


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()), index=True)
    name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False, unique=True, index=True)
    phone = Column(String(20), nullable=False, unique=True, index=True)
    hashed_password = Column(String(255), nullable=False)
    avatar = Column(String(500), nullable=True)
    role = Column(
        Enum(UserRole, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=UserRole.USER,
        index=True,
    )
    agent_id = Column(String, ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, unique=True, index=True)
    is_public_profile = Column(Boolean, default=False, index=True)  # If True, phone/email visible to all
    otp_code = Column(String(6), nullable=True)
    otp_expires_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True, index=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=True, index=True)

    # ─── Phase 6: notification preferences ──────────────────────────
    # Stored ahead of the dispatchers: the inquiry-SMS worker isn't wired
    # yet (routers/contact.py just persists the row today). Defaulting to
    # True so the toggle is opt-out once the dispatch path lands; the
    # other three default False until their channels exist.
    notify_inquiries_sms = Column(Boolean, default=True, nullable=False)
    notify_inquiries_email = Column(Boolean, default=False, nullable=False)
    notify_digest_email = Column(Boolean, default=False, nullable=False)
    notify_push = Column(Boolean, default=False, nullable=False)

    # ─── Phase 8: search defaults (sticky personalization) ──────────
    default_radius_km = Column(Integer, default=10, nullable=True)
    preferred_listing_type = Column(String(16), nullable=True)  # 'rent' | 'sale' | NULL
    language = Column(String(8), default='en', nullable=True)   # 'en' | 'sw'

    # ─── Phase 9: pending contact change (OTP-gated) ────────────────
    # Stored on the user row instead of a separate table to keep the
    # confirm step a single-row update with no JOIN — confirms must be
    # fast (user is watching the spinner). OTP is hashed at rest so a
    # DB dump can't replay codes (fixes the audit's P1 plaintext-OTP
    # finding for THIS code path; the login OTP migration is tracked
    # separately).
    pending_phone = Column(String(20), nullable=True)
    pending_email = Column(String(255), nullable=True)
    pending_contact_otp_hash = Column(String(128), nullable=True)
    pending_contact_expires_at = Column(DateTime(timezone=True), nullable=True)
    pending_contact_kind = Column(String(8), nullable=True)  # 'phone' | 'email'

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # One-to-one: a user with role="agent" links to their Agent profile
    agent = relationship("Agent", backref="user_account", uselist=False, foreign_keys=[agent_id])

    _role_rows = relationship(
        "UserRoleRow",
        cascade="all, delete-orphan",
        lazy="selectin",
        backref="user",
    )

    @property
    def roles(self) -> list[str]:
        rows = [r.role for r in (self._role_rows or [])]
        if rows:
            return rows
        # Fallback for users not yet backfilled into user_roles
        return [self.role.value] if self.role else []

    def has_role(self, role) -> bool:
        target = role.value if isinstance(role, UserRole) else role
        return target in self.roles


class UserRoleRow(Base):
    """ORM mapping for the user_roles association table.

    Kept as a separate class (instead of pure secondary= on a relationship) so we
    can read and write rows imperatively from the role-assignment endpoint.
    """
    __tablename__ = "user_roles"

    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    role = Column(String(20), primary_key=True)
    granted_at = Column(DateTime(timezone=True), server_default=func.now())
